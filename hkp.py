# -*- coding: utf-8 -*-
"""hkp — HKP1 paket motoru (saf, MCP'siz).

oturum-sikistirici MCP sunucusu ve /czip plugin'i TARAFINDAN ORTAK kullanilir.
Amaç: uzun bir Hermes oturumunu yeni oturuma tasinacak kadar küçültmek; okuyan AI
eksiksiz anlayip devam edebilsin. Kayip yok: 'eksiksiz' modda her bayt geri doner.

Format HKP1: "HKP1" + 4B meta_uzunlugu + 4B veri_uzunlugu + LZMA(meta) + LZMA(veri)
Güvenlik: state.db daima READ-ONLY URI; silme/bozma yok.
"""

import json
import lzma
import os
import re
import struct
import sqlite3
import sys
import time

IMZA = b"HKP1"
V = 1
LZMA_SABIT = {"preset": 9 | lzma.PRESET_EXTREME}
JETON_AC = "\u241f"      # ␟
JETON_KAP = "\u241e"     # ␞
JETON_RE = re.compile("\u241f(\\d{1,6})\u241e")
KOD_IPUCU = re.compile(r"(<[a-zA-Z/!]|function |=>|\bdef \b|\bimport \b|```\|\|.*\||=>|\{\{|\}\})")
PARCA_ILET = 40
PAKET_DIZIN = "~/.hermes/session-packs"


def hata(msg):
    return json.dumps({"ok": False, "hata": str(msg)}, ensure_ascii=False)


# ------------------------------------------------------------- girdi ayristirma
def girdi_ayristir(veri):
    """yol / mesaj listesi / sozluk / JSON metni / JSONL / duz metin -> mesaj listesi"""
    if isinstance(veri, list):
        return veri
    if isinstance(veri, dict):
        if isinstance(veri.get("messages"), list):
            return veri["messages"]
        return [veri]
    if isinstance(veri, (bytes, bytearray)):
        veri = veri.decode("utf-8", "replace")
    if not isinstance(veri, str):
        raise ValueError("girdi: dosya yolu, mesaj listesi, sozluk veya metin olmali")
    girdi = os.path.expanduser(veri.strip())
    ham = None
    if len(girdi) < 2048 and "\n" not in girdi and os.path.isfile(girdi):
        with open(girdi, "rb") as f:
            ham = f.read()
    else:
        ham = veri.encode("utf-8")
    if ham.startswith(b"\xef\xbb\xbf"):
        ham = ham[3:]
    metin = ham.decode("utf-8", "replace")
    g = metin.strip()
    if not g:
        return []
    try:
        j = json.loads(g)
        return girdi_ayristir(j)
    except Exception:
        pass
    if g[0] in "[{":
        try:
            kayitlar = [json.loads(s) for s in g.splitlines() if s.strip()]
            if kayitlar and all(isinstance(s, dict) for s in kayitlar):
                return kayitlar
        except Exception:
            pass
    m = re.search(r"<\|HERMES_EXPORT_BLOCK_START\|>\n?(.*?)<\|HERMES_EXPORT_BLOCK_END\|>",
                  g, re.S)
    if m:
        icerikler = re.findall(r"<\|content\|>(.*?)<\|/content\|>", m.group(1), re.S)
        roller = re.findall(r"<\|role\|>(.*?)<\|/role\|>", m.group(1), re.S)
        return [{"role": r, "content": c} for r, c in zip(roller, icerikler)]
    return [{"role": "user", "content": g}]


# ------------------------------------------------------------- sozlesellestirme
class Sozluk(list):
    """sozluk + index — jeton ekleme/arama O(1)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.izin = {s: i for i, s in enumerate(self)}

    def ekle(self, deger):
        if deger in self.izin:
            return self.izin[deger]
        self.append(deger)
        self.izin[deger] = len(self) - 1
        return len(self) - 1


def _soz_ekle(soz, deger):
    if not isinstance(soz, Sozluk):
        soz = Sozluk(soz)
    return soz.ekle(deger)


def _jeton(i):
    return JETON_AC + str(i) + JETON_KAP


def json_metin_ayristir(v):
    """DB'den gelen JSON-metin kolonlarini (tool_calls) sozluge cevir."""
    if isinstance(v, str):
        g = v.strip()
        if g[:1] in "[{":
            try:
                return json.loads(g)
            except Exception:
                return v
    return v


def kayit_yap(m, sabit_adaylari):
    """Tek ham mesaj -> kanonik kisa kayit.
    api_content atilir: icerik + oturuma-ozel memory-context enjeksiyonudur,
    yeni oturumda kendiliginden yenilenir."""
    rol = str(m.get("role") or "?")
    icerik = json_metin_ayristir(m.get("content"))
    if icerik is not None and not isinstance(icerik, str):
        icerik = json.dumps(icerik, ensure_ascii=False, separators=(",", ":"))
    k = {"r": rol}
    if icerik:
        k["c"] = icerik
    tcs = json_metin_ayristir(m.get("tool_calls"))
    if isinstance(tcs, dict):
        tcs = [tcs]
    if tcs:
        sade = []
        for t in tcs:
            t = t or {}
            f = t.get("function") or {}
            g = {}
            if f.get("name"):
                g["n"] = f["name"]
            a = f.get("arguments")
            if isinstance(a, str) and a:
                g["a"] = a
            if t.get("id"):
                g["i"] = t["id"]
            sade.append(g)
        k["tc"] = sade
    r1, r2 = m.get("reasoning"), m.get("reasoning_content")
    if isinstance(r2, str) and r2 == r1:
        r2 = None
    if isinstance(r1, str) and r1:
        k["e"] = r1
    if isinstance(r2, str) and r2:
        k["e2"] = r2
    fr = m.get("finish_reason")
    if fr and fr != "stop":
        k["f"] = fr
    x = {}
    for a in ("id", "tool_call_id", "tool_name", "platform_message_id",
              "timestamp", "token_count", "observed", "compacted"):
        v = m.get(a)
        if v is None or v == "" or v is False or v == []:
            continue
        x[a] = v
    for a, v in x.items():
        sabit_adaylari.setdefault(a, set()).add(json.dumps(v, ensure_ascii=False))
    if x:
        k["x"] = x
    return k


def bosluk_temizle(s):
    """kod-gorunmeyen metinde guvenli gereksiz temizligi."""
    if not isinstance(s, str):
        return s
    if KOD_IPUCU.search(s[:3000] if len(s) > 3000 else s):
        return s.replace("\r\n", "\n").strip()
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def arac_kirp(s, bas, son):
    n = len(s)
    if n <= bas + son + 40:
        return s
    return s[:bas] + f"\n…[{n - bas - son} bayt]…\n" + s[-son:]


def sozluk_gecis(kayitlar, sozluk, alt_min=3, sat_min=24):
    """2 gecisli sozluk:
    1) birebir ayni 40B+ alan degerleri -> sozluk + jeton
    2) ayni satirin 3+ tekrari (24B+ satir) -> satir sozlugu + jeton"""
    def alan_iter(k):
        for a in ("c", "e", "e2"):
            v = k.get(a)
            if isinstance(v, str) and len(v) >= 40:
                yield k, a, v
        for g in k.get("tc") or []:
            v = g.get("a")
            if isinstance(v, str) and len(v) >= 40:
                yield g, "a", v

    degerler = {}
    for k in kayitlar:
        for _, _, v in alan_iter(k):
            h = hash(v)
            degerler[h] = degerler.get(h, 0) + 1

    for k in kayitlar:
        for obj, a, v in list(alan_iter(k)):
            if degerler.get(hash(v), 0) >= 2:
                obj[a] = _jeton(_soz_ekle(sozluk, v))

    satirlar = {}
    for k in kayitlar:
        for a in ("c", "e", "e2"):
            v = k.get(a)
            if isinstance(v, str) and len(v) >= 80 and JETON_AC not in v:
                for s in v.split("\n"):
                    s = s.strip()
                    if len(s) >= sat_min:
                        satirlar[s] = satirlar.get(s, 0) + 1
    tekrarlayan = sorted([s for s, c in satirlar.items() if c >= alt_min],
                         key=len, reverse=True)[:4000]
    if tekrarlayan:
        esleyenler = [(s, _jeton(_soz_ekle(sozluk, s))) for s in tekrarlayan]

        def yerdegistir(k):
            for a in ("c", "e", "e2"):
                v = k.get(a)
                if isinstance(v, str) and len(v) >= 80 and JETON_AC not in v:
                    for s, j in esleyenler:
                        if s in v:
                            v = v.replace(s, j)
                    k[a] = v
        for k in kayitlar:
            yerdegistir(k)
    return sozluk


# ------------------------------------------------------------- paket I/O
def paketle(kayitlar, sozluk, sabitler, baslik=None):
    veri = json.dumps(kayitlar, ensure_ascii=False, separators=(",", ":")) + "\n"
    meta = {"v": V, "soz": list(sozluk),
            "sabit": {k: next(iter(v)) for k, v in sabitler.items() if len(v) == 1},
            "n": len(kayitlar)}
    if baslik:
        meta["baslik"] = baslik
    mbayt = lzma.compress(json.dumps(meta, ensure_ascii=False).encode("utf-8"), **LZMA_SABIT)
    vbayt = lzma.compress(veri.encode("utf-8"), **LZMA_SABIT)
    return mbayt, vbayt


def paket_yaz(yol, mbayt, vbayt):
    with open(yol, "wb") as f:
        f.write(IMZA)
        f.write(struct.pack("<II", len(mbayt), len(vbayt)))
        f.write(mbayt)
        f.write(vbayt)


def paket_oku(yol):
    ham = open(yol, "rb").read()
    if not ham.startswith(IMZA):
        raise ValueError("HKP1 imzasi yok — bu aracla uretilmis bir dosya degil")
    mu, vu = struct.unpack("<II", ham[4:12])
    meta = json.loads(lzma.decompress(ham[12:12 + mu]))
    veri = lzma.decompress(ham[12 + mu:12 + mu + vu]).decode("utf-8")
    return meta, veri


def coz_sozluk(s, sozluk):
    """Jetokenleri sozlukte cozer."""
    if not isinstance(s, str) or not s:
        return s
    for _ in range(8):
        y = JETON_RE.sub(lambda m: sozluk[int(m.group(1))], s)
        if y == s:
            break
        s = y
    return s


def tam_ilet(k, sozluk, reasoning=True):
    """Kisa kayit -> okunur tam ilet sozlugu."""
    it = {"rol": k.get("r")}
    for a, ad in (("c", "icerik"), ("e", "reasoning"), ("e2", "reasoning2"),
                  ("f", "finish_reason"), ("x", "meta")):
        if a in k and not (a in ("e", "e2") and not reasoning):
            it[ad] = coz_sozluk(k[a], sozluk) if a in ("c", "e", "e2") else k[a]
    if "tc" in k:
        it["tool_calls"] = [{"id": g.get("i"), "name": g.get("n", ""),
                             "arguments": coz_sozluk(g.get("a", ""), sozluk)} for g in k["tc"]]
    return it


# ------------------------------------------------------------- yuksuk seviye
def sikistir(mesajlar, cikti, mod="akilli", arac_bas=2000, arac_son=2000,
             baslik=None, kaynak_bayt=None):
    """Mesaj listesi -> .hkp paketi. Sozluk doner (hata durumunda 'hata' anahtari)."""
    sabit_adaylari = {}
    kayitlar = []
    for m in mesajlar:
        k = kayit_yap(m, sabit_adaylari)
        if k is None:
            continue
        for a in ("c", "e", "e2"):
            if a in k:
                k[a] = bosluk_temizle(k[a])
        kayitlar.append(k)
    eksikler = []
    if mod == "akilli" and (arac_bas > 0 or arac_son > 0):
        for i, k in enumerate(kayitlar):
            c = k.get("c")
            if k.get("r") == "tool" and isinstance(c, str):
                yeni = arac_kirp(c, arac_bas, arac_son)
                if yeni != c:
                    eksikler.append({"i": i, "bayt": len(c) - len(yeni)})
                    k["c"] = yeni
    sozluk = Sozluk()
    sozluk_gecis(kayitlar, sozluk)
    mbayt, vbayt = paketle(kayitlar, sozluk, sabit_adaylari, baslik)
    yol = os.path.expanduser(cikti)
    if yol.endswith(".hzip"):
        yol = yol[:-len(".hzip")] + ".hkp"
    elif not yol.endswith(".hkp"):
        yol += ".hkp"
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    paket_yaz(yol, mbayt, vbayt)
    toplam = os.path.getsize(yol)
    kb = kaynak_bayt or len(json.dumps(mesajlar, ensure_ascii=False))
    return {"ok": True, "yol": yol, "paket_bayt": toplam, "kaynak_bayt": kb,
            "oran": round(kb / max(1, toplam), 1), "mesaj": len(kayitlar),
            "sozluk": len(sozluk), "mod": mod, "eksik_bildirim": eksikler or None,
            "parca": max(1, -(-len(kayitlar) // PARCA_ILET))}


def yukle(dosya):
    meta, veri = paket_oku(os.path.expanduser(dosya))
    return meta, json.loads(veri)


def kilavuz(dosya, son_n=6):
    """Paketi ACAMDAN giris: tek satir indeks + son iletiler tam + durum."""
    meta, kayitlar = yukle(dosya)
    sozluk = meta.get("soz", [])

    def ozetle(s, n=120):
        s = coz_sozluk(s, sozluk) if isinstance(s, str) else (str(s) if s is not None else "")
        s = " ".join(s.split())
        return s[:n] + ("…" if len(s) > n else "")

    indeks = []
    for i, k in enumerate(kayitlar):
        sat = {"i": i, "rol": k.get("r")}
        if k.get("c"):
            sat["ozet"] = ozetle(k["c"])
        if k.get("tc"):
            sat["arac"] = [g.get("n", "?") for g in k["tc"]]
        indeks.append(sat)
    son = []
    for k in kayitlar[-son_n:]:
        it = tam_ilet(k, sozluk)
        for a in ("icerik", "reasoning"):
            if a in it and len(it[a]) > 6000:
                it[a] = it[a][:6000] + "…[devami icin mesajlar ile cagir]"
        son.append(it)
    durum = {}
    if kayitlar:
        k = kayitlar[-1]
        durum["son_rol"] = k.get("r")
        durum["son_finish"] = k.get("f")
        durum["kullanici_yaniti_bekliyor"] = (k.get("r") == "user")
    return {"ok": True, "baslik": meta.get("baslik"), "toplam": len(kayitlar),
            "indeks": indeks, "son": son, "durum": durum,
            "talimat": ("Bu paketin TAMAMINI iceri_ac ile okuma. Indeksten ilgili "
                        "mesajlari bul, yalniz onlari mesajlar(dosya, 'bas-bit') ile oku. "
                        "Son iletler yukarida tam verildi; kaldigin yerden devam et.")}


def mesaj_araligi(dosya, aralik):
    meta, kayitlar = yukle(dosya)
    sozluk = meta.get("soz", [])
    a = aralik.strip()
    if "-" in a:
        s, e = a.split("-", 1)
        bas, son = int(s), int(e) + 1
    else:
        bas = int(a); son = bas + 1
    if bas < 0 or son > len(kayitlar) or son <= bas:
        raise ValueError(f"gecersiz aralik '{aralik}' (toplam {len(kayitlar)} mesaj)")
    return [tam_ilet(k, sozluk) for k in kayitlar[bas:son][:80]]


def iceri_ac(dosya, parca=0):
    meta, kayitlar = yukle(dosya)
    sozluk = meta.get("soz", [])
    iletler = [tam_ilet(k, sozluk) for k in kayitlar]
    n = len(iletler)
    toplam_parca = max(1, -(-n // PARCA_ILET))
    if parca and parca > 0:
        if parca > toplam_parca:
            raise ValueError(f"parca {parca} > toplam {toplam_parca}")
        iletler = iletler[(parca - 1) * PARCA_ILET: parca * PARCA_ILET]
    return {"ok": True, "mesaj": len(iletler), "toplam": n, "parca": parca or 1,
            "toplam_parca": toplam_parca,
            "satirlar": "\n".join(json.dumps(x, ensure_ascii=False) for x in iletler)}


# ------------------------------------------------------------- state.db (RO)
def db_yolu():
    return os.environ.get("HERMES_STATE_DB", os.path.expanduser("~/.hermes/state.db"))


def _db():
    yol = db_yolu()
    if not os.path.isfile(yol):
        raise ValueError("state.db yok: " + yol)
    return sqlite3.connect(f"file:{yol}?mode=ro", uri=True, timeout=8)


def baslik_bul(con, sid):
    try:
        row = con.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def oturum_oku(session_id):
    """state.db'den (READ-ONLY) oturum iletlerini + basligi okur."""
    con = _db()
    try:
        sid = oturum_sec(con, session_id)
        cols = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
        secilen = [c for c in cols if c in
                   ("role", "content", "tool_calls", "reasoning", "reasoning_content",
                    "tool_call_id", "tool_name", "id", "timestamp", "finish_reason",
                    "platform_message_id", "token_count", "observed", "compacted",
                    "effect_disposition", "reasoning_details", "reasoning_co")]
        alanlar = ", ".join(f'"{c}"' for c in secilen)
        satirlar = con.execute(
            f"SELECT {alanlar} FROM messages WHERE session_id=? ORDER BY id",
            (sid,)).fetchall()
        baslik = baslik_bul(con, sid)
    finally:
        con.close()
    return sid, [dict(zip(secilen, s)) for s in satirlar], baslik


def oturum_sec(con, session_id):
    if session_id in ("son", "en-son"):
        return con.execute("SELECT session_id FROM messages GROUP BY session_id "
                           "ORDER BY MAX(COALESCE(timestamp,0)) DESC LIMIT 1").fetchone()[0]
    if session_id in ("en-uzun", "en-buyuk"):
        return con.execute("SELECT session_id FROM messages GROUP BY session_id "
                           "ORDER BY SUM(COALESCE(LENGTH(content),0)+COALESCE(LENGTH(reasoning),0)) DESC LIMIT 1"
                           ).fetchone()[0]
    es = con.execute("SELECT DISTINCT session_id FROM messages WHERE session_id LIKE ?",
                     (session_id + "%",)).fetchall()
    if len(es) != 1:
        raise ValueError(f"'{session_id}' -> {len(es)} eslesme: "
                         + ", ".join(s[0] for s in es[:6]) if es else f"'{session_id}': eslesme yok")
    return es[0][0]


def son_paket(dizin=None):
    """Paket dizinindeki en yeni .hkp yolu (yoksa None)."""
    d = os.path.expanduser(dizin or PAKET_DIZIN)
    if not os.path.isdir(d):
        return None
    dosyalar = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".hkp")]
    return max(dosyalar, key=os.path.getmtime) if dosyalar else None


# ------------------------------------------------------------- CLI
def _main(argv):
    """czip komutlari:
      czip paketle <session_id|son|en-uzun> [--eksiksiz]   -> oturumu paketle
      czip oku <paket.hkp|son>                             -> indeks + son 6 + durum
      czip aralik <paket.hkp|son> <bas-bit>                -> secili araligi tam oku
    """
    if not argv or argv[0] in ("-h", "--help", "yardim"):
        print("Kullanim:\n"
              "  czip paketle <session_id|son|en-uzun> [--eksiksiz]\n"
              "  czip oku <paket.hkp|son>\n"
              "  czip aralik <paket.hkp|son> <bas-bit>")
        return 0
    emir, kalan = argv[0], argv[1:]
    if emir == "paketle":
        if not kalan:
            print("HATA: session_id gerekir (veya 'son' / 'en-uzun')")
            return 2
        sid = kalan[0]
        mod = "eksiksiz" if "--eksiksiz" in kalan[1:] else "akilli"
        sid, mesajlar, baslik = oturum_oku(sid)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", (baslik or sid)[:48]).strip("-") or sid
        yol = os.path.join(d, f"{temiz}-{time.strftime('%Y%m%d-%H%M%S')}.hkp")
        r = sikistir(mesajlar, yol, mod, baslik=baslik)
        kirp = len(r.get("eksik_bildirim") or [])
        print(f"PAKETLENDI: {r['yol']}\n  ilet={r['mesaj']}  {r['kaynak_bayt']}->{r['paket_bayt']}B"
              f"  oran={r['oran']}x  parca={r['parca']}"
              + (f"  kirpilan_arac={kirp}" if kirp else "")
              + f"\n  sonraki: czip oku {r['yol']}")
        return 0
    if emir == "oku":
        yol = kalan[0] if kalan else None
        if not yol or yol == "son":
            yol = son_paket()
        if not yol:
            print("HATA: paket yok — once: czip paketle <id>")
            return 2
        g = kilavuz(os.path.expanduser(yol))
        print(f"PAKET: {g['baslik']} ({g['toplam']} ilet)")
        print("INDEKS:")
        for s in g["indeks"]:
            ar = f"[{','.join(s['arac'])}]" if s.get("arac") else ""
            print(f"{s['i']}|{s['rol']}{ar}|{s.get('ozet', '')}")
        print("\n=== SON ILETLER (tam) ===")
        for it in g["son"]:
            t = it.get("icerik") or ""
            print(json.dumps({k: (v[:700] if isinstance(v, str) else v)
                              for k, v in it.items()}, ensure_ascii=False))
        print("\n=== DURUM ===")
        print(json.dumps(g["durum"], ensure_ascii=False))
        print("\nKURAL: " + g["talimat"])
        return 0
    if emir == "aralik":
        if len(kalan) < 2:
            print("HATA: kullanim -> czip aralik <paket.hkp|son> <bas-bit>")
            return 2
        yol = kalan[0]
        if yol == "son":
            yol = son_paket()
        if not yol:
            print("HATA: paket yok")
            return 2
        try:
            iletler = mesaj_araligi(os.path.expanduser(yol), kalan[1])
        except (ValueError, IndexError) as e:
            print("HATA:", e)
            return 2
        for it in iletler:
            print(json.dumps(it, ensure_ascii=False))
        return 0
    print(f"Bilinmeyen emir: {emir}  (yardim: czip --help)")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
