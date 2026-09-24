# -*- coding: utf-8 -*-
"""hkp — HKP1 paket motoru (saf, MCP'siz).

oturum-sikistirici MCP sunucusu ve /czip plugin'i TARAFINDAN ORTAK kullanilir.
Amaç: uzun bir Hermes oturumunu yeni oturuma tasinacak kadar küçültmek; okuyan AI
eksiksiz anlayip devam edebilsin. Kayip yok: 'eksiksiz' modda her bayt geri doner
(bu modda arac-ciktisi kirpma, Jev budamasi ve tekrar ayiklama DEVRE DISI).

Format HKP1: "HKP1" + 4B meta_uzunlugu + 4B veri_uzunlugu + LZMA(meta) + LZMA(veri)
Güvenlik: state.db daima READ-ONLY URI; silme/bozma yok.
"""

import hashlib
import json
import lzma
import os
import random
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
PAKET_DIZIN = "~/007-HERMES/05-CIKTILAR/oturum-paketleri"
KAYIT_YOL = "~/007-HERMES/05-CIKTILAR/oturum-paketleri/kayit.json"
ARAMA_MAX_SATIR = 8


def _kayit_oku():
    """id -> yol kayit tablosu (bozuksa bos dict; okunur-kirilir, kayip tolere)."""
    try:
        with open(os.path.expanduser(KAYIT_YOL), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _kayit_yaz(kayit):
    yol = os.path.expanduser(KAYIT_YOL)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, indent=1)
    os.replace(gecici, yol)


def id_ata(yol, baslik=None):
    """Pakete 6 haneli kisA id ver; kayit.json'a yazilir. Tekrar atarsa ayni id doner."""
    yol = os.path.abspath(os.path.expanduser(yol))
    kayit = _kayit_oku()
    mevcut = {(v.get("yol") if isinstance(v, dict) else v): k
              for k, v in kayit.items()}
    if yol in mevcut:
        return mevcut[yol]
    rnd = random.Random(time.time_ns() ^ os.getpid())
    harfler = "abcdefghjkmnpqrstuvwxyz23456789"  # 0/o, l/1, i yok — telaffuz/okuma temiz
    for _ in range(200):
        kid = "".join(rnd.choice(harfler) for _ in range(6))
        if kid not in kayit:
            kayit[kid] = {"yol": yol, "baslik": (baslik or "")[:120], "t": time.strftime("%Y-%m-%d %H:%M")}
            _kayit_yaz(kayit)
            return kid
    raise RuntimeError("id havuzu tukendi")


def id_coz(girdi):
    """'son', 6 haneli id veya yol -> paket yolu (yoksa None)."""
    if not girdi or girdi == "son":
        return son_paket()
    kayit = _kayit_oku()
    if girdi in kayit:
        return kayit[girdi].get("yol")
    return os.path.abspath(os.path.expanduser(girdi))


def hata(msg):
    return json.dumps({"ok": False, "hata": str(msg)}, ensure_ascii=False)


# ------------------------------------------------------------- girdi ayristirma
def girdi_ayristir(veri):
    """yol / mesaj listesi / sozluk / JSON metni / JSONL / duz metin -> mesaj listesi"""
    if isinstance(veri, list):
        import ccd_dokum as _cc
        if _cc.claude_dokumu_mu(veri):
            return _cc.satirlardan(veri)[0]
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
        kayitlar, bozuk = [], 0
        for s in g.splitlines():
            if not s.strip():
                continue
            try:
                kayitlar.append(json.loads(s))
            except Exception:
                bozuk += 1
        if kayitlar and all(isinstance(s, dict) for s in kayitlar):
            import ccd_dokum as _cc
            # Claude Code dokumu yarim yazilmis son satir tasiyabilir: tolere et.
            if _cc.claude_dokumu_mu(kayitlar):
                return _cc.satirlardan(kayitlar)[0]
            if not bozuk:
                return kayitlar
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


# ------------------------------------------------------- karar kapisi (opsiyonel)
# Motor: Laya (YEREL, varsayilan, 23.09.2026) ya da Jev (bulut, eski) — bkz. karar.py.
# Arac ciktilarini tek tek 'gerekiyor mu?' diye karar motoruna TOPLU sorar:
# olu olanlar pakete hic girmez, gerekenler OLDUGU GIBI kalir (ozet yok).
# Hata/timeout -> sessiz fallback (statik dilim). state.db'ye asla dokunulmaz;
# 'silme' yalniz paketten cikarmadir, kaynak durur.
JEV_API = "https://api.typesafe.ai/v1/systemone"
JEV_TOPLU = 20       # tek istekteki soru sayisi (batch ~9x tasarruf)
JEV_AJ = 0.30        # Jev: altindaki noul -> olu sayilir (Laya: 0.15, karar.ESIKLER)
JEV_TUT = 0.55       # ustundeki noul -> tam kalir, statik dilim uygulanmaz
JEV_MAX = 240        # oturum basina degerlendirilecek en fazla arac ciktilari
JEV_MIN_BAYT = 1200  # altindaki kucuk ciktilar sorgulanmaz (kazanctan dusuk)
JEV_ONEK = 1400      # soruya gidecek ornek: bas dilimi
JEV_SON = 300        # soruya gidecek ornek: son dilim
JEV_BUTCE_SN = 90    # toplam ag butcesi — asilirsa kalan statik dilime doner
SILINDI_ISARET = re.compile(r"\[(JEV|LAYA)-SILINDI ")
HATA_IPUCU = re.compile(
    r"(?i)(error|fail(ed|ure)?|exception|traceback|panic|core dump|segfault"
    r"|hata|başarısız|basarisiz|çöktü|cobtu|denied|yasak|yok: not found|No such file)")


def _jev_anahtar():
    """TYPESAFE_API_KEY: once ortam, sonra HERMES_HOME/.env (czip wrapper'siz terminal)."""
    k = os.environ.get("TYPESAFE_API_KEY", "")
    if k:
        return k.strip()
    yol = os.path.join(os.environ.get("HERMES_HOME",
                                      os.path.expanduser("~/.hermes")), ".env")
    try:
        with open(yol, encoding="utf-8") as f:
            for satir in f:
                m = re.match(r"\s*TYPESAFE_API_KEY\s*=\s*(.+?)\s*$", satir)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def _jev_batch(key, durum, sorular, timeout):
    """{qid: instructions} -> {qid: noul}. Ag/HTTP hatasi ValueError firlatir."""
    import urllib.request
    import urllib.error
    govde = json.dumps({"model": "jev-latest", "state": durum,
                        "questions": {q: {"type": "noul", "instructions": tal}
                                      for q, tal in sorular.items()}},
                       ensure_ascii=False).encode("utf-8")
    istek = urllib.request.Request(
        JEV_API, data=govde,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(istek, timeout=timeout) as yanit:
            veri = json.loads(yanit.read())
    except urllib.error.HTTPError as e:
        raise ValueError(f"http_{e.code}")
    except Exception as e:
        raise ValueError(str(e)[:80])
    out = {}
    for q, a in (veri.get("answers") or {}).items():
        if isinstance(a, dict) and a.get("noul") is not None:
            out[q] = float(a["noul"])
    if not out:
        raise ValueError("bos_answers")
    return out


def karar_kararlar(kayitlar, baslik, son_kullanici, esik_at=None, esik_tut=None,
                   timeout=25):
    """Buyuk arac ciktilarini karar motoruna (Laya/Jev) toplu sorar.
    Doner: (karar: {idx: (eylem, noul)}, bilgi: dict)
      eylem: 'sil' | 'tut' | 'kirp'  — hata durumunda karar bos, bilgi['hata'] dolu."""
    import karar as _k
    karar, bilgi = {}, {"motor": _k.motor_adi()}
    es = _k.esikler()
    esik_at = es["sil"] if esik_at is None else esik_at
    esik_tut = es["tut"] if esik_tut is None else esik_tut
    # durum (state): paket basligi + son kullanici mesajlari — Jev 'ne yapilmakta' bilsin
    parcalar = []
    if baslik:
        parcalar.append("Task: " + str(baslik)[:200])
    for s in (son_kullanici or [])[-3:]:
        s = " ".join(str(s).split())
        if s:
            parcalar.append("User: " + s[:200])
    durum = " | ".join(parcalar)[:900] or "Session transcript compression."

    soru_sablon = ("Tool output from a completed working session. "
                   "Is this tool output still needed verbatim to understand, continue, "
                   "or reproduce the session's work (decision, result, config value, "
                   "error info, final content)? Verbose logs or redundant echoes are not.\n"
                   "SAMPLE:\n")
    # degerlendirme adaylari
    adaylar = []
    for i, k in enumerate(kayitlar):
        c = k.get("c")
        if k.get("r") == "tool" and isinstance(c, str) and len(c) >= JEV_MIN_BAYT:
            adaylar.append(i)
            if len(adaylar) >= JEV_MAX:
                break
    # hata icerikli ciktilar sorgulanmadan KORUNUR (debug icin kritiktir)
    korunacak = set()
    sorgulanacak = []
    for i in adaylar:
        ornek = kayitlar[i]["c"]
        if HATA_IPUCU.search(ornek[:6000]):
            korunacak.add(i)
            karar[i] = ("tut", None)
        else:
            sorgulanacak.append(i)
    bilgi["aday"] = len(adaylar)
    bilgi["hata_ipucu_korunan"] = len(korunacak)

    t0 = time.time()
    anahtarsiz = list(sorgulanacak)
    for bas in range(0, len(anahtarsiz), JEV_TOPLU):
        if time.time() - t0 > JEV_BUTCE_SN:
            bilgi["kesildi"] = True
            break
        dilim = anahtarsiz[bas:bas + JEV_TOPLU]
        sorular = {}
        qmap = {}
        for j, i in enumerate(dilim):
            c = kayitlar[i]["c"]
            ornek = c[:JEV_ONEK] + ("\n[…]\n" + c[-JEV_SON:] if len(c) > JEV_ONEK + JEV_SON else "")
            sorular[f"n{j}"] = soru_sablon + ornek
            qmap[f"n{j}"] = i
        try:
            cevap, bilgi["motor"] = _k.sor(durum, sorular, timeout)
        except ValueError as e:
            bilgi["hata"] = str(e)
            break
        for q, n in cevap.items():
            i = qmap.get(q)
            if i is None:
                continue
            if n < esik_at:
                karar[i] = ("sil", round(n, 3))
            elif n >= esik_tut:
                karar[i] = ("tut", round(n, 3))
            else:
                karar[i] = ("kirp", round(n, 3))
    bilgi["sorgu"] = sum(1 for v in karar.values() if v[1] is not None)
    bilgi["sil"] = sum(1 for v in karar.values() if v[0] == "sil")
    bilgi["tut"] = sum(1 for v in karar.values() if v[0] == "tut")
    bilgi["kirp"] = sum(1 for v in karar.values() if v[0] == "kirp")
    bilgi["sure_sn"] = round(time.time() - t0, 1)
    return karar, bilgi


jev_kararlar = karar_kararlar  # geriye uyum: eski ad


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
def paketle(kayitlar, sozluk, sabitler, baslik=None, ek_meta=None):
    veri = json.dumps(kayitlar, ensure_ascii=False, separators=(",", ":")) + "\n"
    meta = {"v": V, "soz": list(sozluk),
            "sabit": {k: next(iter(v)) for k, v in sabitler.items() if len(v) == 1},
            "n": len(kayitlar)}
    if baslik:
        meta["baslik"] = baslik
    if ek_meta:
        meta.update(ek_meta)
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


def meta_oku(yol):
    """Yalniz meta (baslik, n, yon karti, kaynak) — govdeyi acmadan, hizli."""
    with open(os.path.expanduser(yol), "rb") as f:
        bas = f.read(12)
        if not bas.startswith(IMZA):
            raise ValueError("HKP1 imzasi yok")
        mu, _ = struct.unpack("<II", bas[4:12])
        return json.loads(lzma.decompress(f.read(mu)))


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
             baslik=None, kaynak_bayt=None, jev=False, kaynak=None):
    """Mesaj listesi -> .hkp paketi. Sozluk doner (hata durumunda 'hata' anahtari).
    jev=True: 'akilli' modda buyuk arac ciktilarini karar kapisina (varsayilan
    YEREL Laya; bkz. karar.py) toplu sor; olu olanlar paketten cikarilir (iz
    birakarak), gerekenler OLDUGU GIBI kalir, ortalar statik dilime duser.
    Motora ulasilamazsa mevcut statik dilim davranisina doner. (Parametre adi
    geriye uyum icin 'jev' kaldi.)
    kaynak: {"sid", "proje", "cwd", "dal"} — temizlik (ayni oturumun eski
    paketleri) ve hafiza gunlugu (proje bazli hatirlama) icin meta'ya yazilir."""
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
    jev_bilgi = None
    kararlar = {}
    if jev and mod == "akilli":
        son_kul = [k.get("c") for k in kayitlar if k.get("r") == "user"]
        kararlar, jev_bilgi = karar_kararlar(kayitlar, baslik, son_kul)
    for i, k in enumerate(kayitlar):
        c = k.get("c")
        if k.get("r") != "tool" or not isinstance(c, str):
            continue
        eylem = kararlar.get(i, (None, None))[0]
        if eylem == "sil":
            noul = kararlar[i][1]
            motor = (jev_bilgi or {}).get("motor", "laya").upper()
            k["c"] = f"[{motor}-SILINDI noul={noul} — kaynak girdide duruyor]"
            eksikler.append({"i": i, "bayt": len(c) - len(k["c"]), "jev": "sil"})
        elif eylem == "tut":
            continue  # oldugu gibi kalir, statik dilim uygulanmaz
        elif mod == "akilli" and (arac_bas > 0 or arac_son > 0):
            yeni = arac_kirp(c, arac_bas, arac_son)
            if yeni != c:
                eksikler.append({"i": i, "bayt": len(c) - len(yeni),
                                 **({"jev": "kirp"} if eylem == "kirp" else {})})
                k["c"] = yeni
    # ── ARAC CIKTISI TEKRAR AYIKLAMA (2026-09-20 olcum: arac ciktilari toplam
    # baytin %71'i, ve bunlarin %78'i BIREBIR ayni metin). Ikinci ve sonraki
    # kopyalar geri-basvuruya cevrilir. Okuyan AI icin kayip yok, hatta daha
    # iyi: "bu #N ile ayni" bilgisi metni tekrar okumaktan daha kullanisli.
    # 'eksiksiz' modda da guvenli — atilan bilgi yok, isaret cozulebilir.
    ilk_gorulen = {}
    tekrar_sayisi = 0
    tekrar_bayt = 0
    # 'eksiksiz' modun SOZU "her bayt geri doner" — tekrar isaretleme bu sozu
    # bozar (isaret cozulur ama ham bayt aynen geri gelmez). Bu yuzden ayiklama
    # YALNIZ 'akilli' modda calisir; eksiksiz istendiginde hic dokunulmaz.
    for i, k in enumerate(kayitlar) if mod != "eksiksiz" else []:
        c = k.get("c")
        if k.get("r") != "tool" or not isinstance(c, str) or len(c) < 200:
            continue
        if SILINDI_ISARET.match(c) or c.startswith("[AYNI-#"):
            continue
        h = hashlib.blake2b(c.encode("utf-8", "replace"), digest_size=16).digest()
        ilk = ilk_gorulen.get(h)
        if ilk is None:
            ilk_gorulen[h] = i
            continue
        k["c"] = f"[AYNI-#{ilk} — bu arac ciktisi {ilk} numarali iletle birebir ayni; gerekirse orayi oku]"
        tekrar_sayisi += 1
        tekrar_bayt += len(c) - len(k["c"])

    # YON KARTI: sozluk jetonlamasindan ONCE (duz metin uzerinde) cikarilir.
    try:
        import yon as _yon
        yon_karti = _yon.kart(kayitlar, baslik, karar_kapisi=jev and mod == "akilli")
    except Exception:
        yon_karti = None
    ek = {"mod": mod, "t": time.strftime("%Y-%m-%d %H:%M")}
    if yon_karti:
        ek["yon"] = yon_karti
    if kaynak:
        ek["kaynak"] = {k: v for k, v in kaynak.items() if v}
    sozluk = Sozluk()
    sozluk_gecis(kayitlar, sozluk)
    mbayt, vbayt = paketle(kayitlar, sozluk, sabit_adaylari, baslik, ek)
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
            "tekrar": tekrar_sayisi, "tekrar_bayt": tekrar_bayt,
            "jev": jev_bilgi, "yon": yon_karti, "baslik": baslik, "kaynak": kaynak,
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


def paket_ara(dosya, sorgu, max_satir=ARAMA_MAX_SATIR):
    """Paket iceriginde kelime arar — RAG mantigi: paket ACILMAZ, sadece bulunan
    mesajlarin indeks satirlari + 120 karakterlik cevre parcalari doner.
    Sonuc: [{'i': idx, 'rol': r, 'eslesme': '...cevre...'}]
    """
    meta, kayitlar = yukle(dosya)
    sozluck = meta.get("soz", [])
    kelimeler = [w for w in re.findall(r"\w+", str(sorgu).lower(), re.UNICODE) if len(w) >= 2]
    if not kelimeler:
        raise ValueError("sorguda aranacak kelime yok")
    sonuclar = []
    for i, k in enumerate(kayitlar):
        icerik = coz_sozluk(str(k.get("c") or ""), sozluck)
        alt = icerik.lower()
        if all(kw in alt for kw in kelimeler):
            poz = alt.find(kelimeler[0])
            cevre = icerik[max(0, poz - 40): poz + 120].replace("\n", " ")
            sonuclar.append({"i": i, "rol": k.get("r"), "eslesme": cevre})
            if len(sonuclar) >= max_satir:
                break
    return sonuclar


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


# ============================================================ RAG-ONCELIKLI OKUMA
# Olcum (2026-09-20, 3421 iletlik gercek oturum):
#   kilavuz()      -> 93.417 token  (%98'i satir satir INDEKS)
#   harita()       ->  ~1.500 token (asagida), + gerektikce ara()/mesaj_araligi()
# Paket dosyasinin kendisi context'e HIC girmez; maliyet yalniz bu ciktidir.
# Bu yuzden sikistirma orani token maliyetini DEGISTIRMEZ — okunan sey degistirir.
def harita(dosya, son_n=6, istek_max=40):
    """Ince harita: is tanimlari + arac histogrami + son iletler + arama ipucu.

    Tam indeks YOK. Okuyan AI once bunu alir, sonra ara()/aralik() ile yalnizca
    ihtiyaci olan yeri ceker (RAG). Boylece 90k token -> ~1.5k token.
    """
    meta, kayitlar = yukle(dosya)
    sozluk = meta.get("soz", [])
    roller, araclar = {}, {}
    istekler = []
    for i, k in enumerate(kayitlar):
        r = k.get("r", "?")
        roller[r] = roller.get(r, 0) + 1
        for tc in (k.get("tc") or []):
            n = tc.get("n")
            if n:
                araclar[n] = araclar.get(n, 0) + 1
        if r == "user":
            c = coz_sozluk(k.get("c") or "", sozluk)
            c = " ".join(str(c).split())
            if c and not c.startswith("[") and not c.startswith("──"):
                istekler.append((i, c[:96]))
    tekrar = sum(1 for k in kayitlar
                 if isinstance(k.get("c"), str) and k["c"].startswith("[AYNI-#"))
    tam_karakter = sum(len(json.dumps(tam_ilet(k, sozluk), ensure_ascii=False))
                       for k in kayitlar)
    return {
        "tam_karakter": tam_karakter,
        "baslik": meta.get("baslik") or meta.get("b") or os.path.basename(dosya),
        "yon": meta.get("yon"),
        "toplam": len(kayitlar),
        "roller": roller,
        "araclar": sorted(araclar.items(), key=lambda x: -x[1])[:12],
        "tekrar_isaretli": tekrar,
        "istekler": istekler[-istek_max:],
        "istek_toplam": len(istekler),
        "son": [tam_ilet(k, sozluk) for k in kayitlar[-son_n:]],
        "talimat": ("INDEX IS NOT LOADED. This is a map, not the transcript. "
                    "To get content: `czip ara <id> \"<query>\"` for keyword hits, "
                    "then `czip aralik <id> <a-b>` for the exact messages. "
                    "Never dump the whole package. Work from the last messages below."),
    }


# --- CLI verb aliases: English primary, Turkish kept for backward compatibility ---
_ALIAS = {
    "pack": "paketle",
    "read": "oku",
    "map": "harita",
    "search": "ara",
    "grep": "ara",
    "archive-search": "arsivara",
    "asearch": "arsivara",
    "range": "aralik",
    "merge": "birlestir",
    "list": "listele",
    "undo": "gerial",
    "settings": "ayar",
    "config": "ayar",
    "help": "yardim",
    "around": "cevre",
    "claude": "cc",
    "direction": "yon",
    "clean": "temizle",
    "cleanup": "temizle",
    "journal": "gunluk",
    "recall": "hatirla",
    "brief": "brifing",
}

# Output language: English by default; CZIP_LANG=tr restores Turkish.
LANG = (os.environ.get("CZIP_LANG") or "en").strip().lower()[:2]


def _k_fmt(n):
    return "%.1fk" % (n / 1000.0) if n >= 1000 else str(n)


def T(en, tr):
    """Pick the output string for the active language."""
    return tr if LANG == "tr" else en


# ------------------------------------------------------------- CLI
def _main(argv):
    """czip komutlari:
      czip paketle <session_id|son|en-uzun|cc:son> [--eksiksiz] [--laya]   -> oturumu paketle
      czip oku <paket.hkp|son>                             -> indeks + son 6 + durum
      czip aralik <paket.hkp|son> <bas-bit>                -> secili araligi tam oku
    --laya (eski: --jev): akilli modda buyuk arac ciktilarini karar kapisina
    (yerel Laya; bkz. karar.py) sor; oluler paketten cikar.
    """
    if not argv or argv[0] in ("-h", "--help", "yardim"):
        print("Kullanim:\n"
              "  czip paketle <session_id|son|en-uzun|DOSYA|cc:son|cc:<uuid>> [--eksiksiz] [--laya]\n"
              "                 [--oto] ayni isi yapanlari da ayni pakete al\n"
              "                 [--oto-pasif] ayrica kaynaklari kapat+arsivle\n"
              "  czip oku <paket.hkp|son>\n"
              "  czip aralik <paket.hkp|son> <bas-bit>\n"
              "  czip birlestir [oto|<id1> <id2> ...] [--laya] [--gun=7] [--pasif-yok]\n"
              "  czip cevre <id|son> <i> [--n=3] -> i'nin cevresi (i-n..i+n tam metin)\n"
              "  czip cc [--n=10]        -> Claude Code/Desktop oturumlari (cc:<uuid> ile paketle)\n"
              "  --laya: karar kapisi (yerel Laya; eski ad --jev). Motor: czip ayar motor laya|jev\n"
              "  czip yon <id|son>       -> yon karti: hedef, kararlar, acik isler, SONRAKI ADIM\n"
              "  czip gunluk [--n=10]    -> ne yaptim? paket gunlugu (proje bazli)\n"
              "  czip brifing [--cwd=DIR]-> acilis brifingi (hook'un verdigi metin)\n"
              "  czip hatirla \"istek\"   -> bu istekle ilgili gecmis is (RAG, alaka kapili)\n"
              "  czip temizle [--uygula] [geri [ZAMAN]] -> haftalik temizlik (varsayilan: sadece plan)\n"
              "  czip hook <Olay>        -> Claude Code hook girisi (stdin JSON)\n"
              "  czip auto64 | auto128 | auto<N> | auto off -> her N bin tokende otomatik paketle\n"
              "  czip harita <id|son>    -> RAG haritasi (~1.5k token; oku'nun ucuz hali)\n"
              "  czip ayar [esik 50|kademe 50,75,90|oto on]  -> thresholds & auto mode\n"
              "  czip index [--full]     -> build the searchable store over all packages\n"
              "  czip asearch \"<query>\"   -> search the whole archive (long-term memory)\n"
              "  czip gerial [<dosya>]   -> pasife alinan oturumlari geri ac")
        return 0
    emir, kalan = argv[0], argv[1:]
    # Karar kapisi bayragi: --laya (yerel, varsayilan motor) / --karar; eski --jev
    # ayni kapiyi acar — motor ayar/env'den gelir (karar.py), bayraktan degil.
    kalan = ["--jev" if a in ("--laya", "--karar", "--gate") else a for a in kalan]
    # English is the primary CLI; the original Turkish verbs keep working.
    emir = _ALIAS.get(emir, emir)
    # /czip_50 , /czip_%75 , czip 50%  -> shorthand for "ayar esik <n>"
    _auto = re.fullmatch(r"(?:czip[-_]?)?auto[-_]?(\d{1,4})k?", emir)
    if _auto:
        # czip auto64 / auto128 / czip-auto256: N bin tokende bir kendiliginden paketle
        import ayar as _a
        try:
            a = _a.auto_kur(int(_auto.group(1)))
        except ValueError as e:
            print("HATA:", e)
            return 2
        k = a["adim_token"] // 1000
        print(T("czip-auto%d ON: packs automatically at every %dk tokens of context "
                "(%dk, %dk, %dk ...). Off: czip auto off",
                "czip-auto%d ACIK: baglam her %dk token buyudukce kendiliginden paketler "
                "(%dk, %dk, %dk ...). Kapat: czip auto off") % (k, k, k, 2 * k, 3 * k))
        return 0
    if emir in ("auto", "oto") and kalan and kalan[0].lower() in ("off", "kapat", "0"):
        import ayar as _a
        _a.auto_kur(0)
        _a.yaz(oto=False)
        print(T("auto mode off — back to percentage tiers (%s)",
                "oto mod kapali — yuzde kademelerine donuldu (%s)")
              % ", ".join("%%%d" % round(x * 100) for x in _a.oku()["kademeler"]))
        return 0
    if emir in ("auto", "oto") and not kalan:
        import ayar as _a
        a = _a.oku()
        print(T("auto mode: ", "oto mod: ") + (
            "czip-auto%d" % (a["adim_token"] // 1000) if a.get("adim_token") else
            T("off (percentage tiers)", "kapali (yuzde kademeleri)")))
        print(T("presets: ", "hazir: ") + "  ".join("czip auto%d" % k for k in _a.AUTO_HAZIR)
              + T("   any N works: czip auto96", "   her N olur: czip auto96"))
        return 0
    _m = re.fullmatch(r"%?(\d{1,3})%?", emir)
    if _m:
        kalan = ["esik", _m.group(1)] + list(kalan)
        emir = "ayar"
    elif emir in ("auto", "oto"):
        kalan = ["oto", (kalan[0] if kalan else "on")]
        emir = "ayar"

    if emir == "birlestir":
        # Ayni isi yapan 2+ oturumu TEK pakette birlestirir (bkz. birlestir.py).
        # Argumansiz: salt-okunur aday taramasi, hicbir sey yazmaz.
        import birlestir as _b
        jev = "--jev" in kalan
        mod = "eksiksiz" if "--eksiksiz" in kalan else "akilli"
        gun = 7
        for a in kalan:
            if a.startswith("--gun="):
                try:
                    gun = max(1, int(a.split("=", 1)[1]))
                except ValueError:
                    pass
        idler = [a for a in kalan if not a.startswith("--") and a != "oto"]
        oto = "oto" in kalan
        if len(idler) < 2:
            _, ciftler = _b.adaylari_bul(gun=gun)
            if not ciftler:
                print(f"Son {gun} gunde birlestirilecek benzer oturum yok.")
                return 0
            gruplar, iz = _b.gruplari_kur(ciftler, jev=jev)
            jb = iz.get("jev") or {}
            print(f"ADAY TARAMASI — son {gun} gun, {len(ciftler)} benzer cift"
                  + (f"  | {jb.get('motor', 'karar')}: {jb.get('hata') or str(jb.get('cevap')) + ' cevap'}"
                     if jev else ""))
            for k in iz["kararlar"][:12]:
                print(f"  {'KABUL' if k['kabul'] else '  red'}  {k['a']} <-> {k['b']}"
                      f"  {k['kaynak']}={k['skor']:.2f}")
                print(f"        A: {k['a_ozet']}")
                print(f"        B: {k['b_ozet']}")
            if not gruplar:
                print("\nEsigi gecen grup yok.")
                return 0
            print("\nGRUPLAR:")
            for i, g in enumerate(gruplar, 1):
                print(f"  {i}) " + "  ".join(g))
            if not oto:
                print("\nUygulamak icin: czip birlestir " + " ".join(gruplar[0])
                      + (" --laya" if jev else ""))
                return 0
            idler = gruplar[0]
            print("\noto: 1. grup birlestiriliyor...")
        mesajlar, baslik, rapor = _b.oturumlari_birlestir(idler)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", baslik[:48]).strip("-") or "birlesik"
        yol = os.path.join(d, f"BIRLESIK-{temiz}-{time.strftime('%Y%m%d-%H%M%S')}.hkp")
        r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev,
                     kaynak={"sid": "birlesik:" + "+".join(sorted(idler)), "proje": baslik[:40]})
        kid = id_ata(r["yol"], baslik)
        import hafiza as _hz
        _hz.kaydet(r, kid, r.get("kaynak"))
        print(f"BIRLESTIRILDI: {r['yol']}\n  ID={kid}")
        for x in rapor["ayrinti"]:
            print(f"    - {x['sid']}  {x['ilet']} ilet")
        print(f"  {rapor['kaynak_ilet']} -> {rapor['birlesik_ilet']} ilet"
              f"  (yinelenen atilan: {rapor['yinelenen_atilan']})")
        print(f"  {r['kaynak_bayt']}->{r['paket_bayt']}B  oran={r['oran']}x")
        print(f"  oku: czip oku {kid}")
        if "--pasif-yok" in kalan or not oto:
            print("  NOT: kaynak oturumlar state.db'de dokunulmadan duruyor.")
            if not oto:
                print("  Kaynaklari pasife de almak icin: czip birlestir oto --laya")
        else:
            try:
                pr = _b.pasife_al(idler, r["yol"], kid, sebep="czip_merge")
                print(f"  PASIFE ALINDI: {len(pr['pasif'])} oturum kapatildi+arsivlendi")
                for x in pr["atlanan"]:
                    print(f"    atlandi: {x['sid']} ({x['neden']})")
                print(f"  GERI AL: czip gerial {os.path.basename(pr['gerial'])}")
                print("  (hicbir ileti silinmedi — oturumlar yalnizca listeden gizlendi)")
            except Exception as _e:
                print(f"  ! PASIFE ALMA BASARISIZ: {str(_e)[:90]}")
                print("    Paket URETILDI ama kaynak oturumlar ACIK kaldi — elle kapat.")
        print("\n  DEVIR TALIMATI (yeni oturumda ilk is):")
        print(f"    czip oku {kid}  -> kalan acik isi tespit et, kaldigi yerden devam et.")
        return 0
    if emir == "gerial":
        import birlestir as _b
        if not kalan:
            d = os.path.expanduser(_b.GERIAL_DIZIN)
            if not os.path.isdir(d) or not os.listdir(d):
                print("Geri alinacak kayit yok.")
                return 0
            print("Geri alma kayitlari:")
            for f in sorted(os.listdir(d), reverse=True)[:10]:
                print("  " + f)
            print("\nKullanim: czip gerial <dosya>")
            return 0
        g = _b.geri_al(kalan[0])
        print("GERI ALINDI: %d oturum yeniden aktif" % len(g["geri_alinan"]))
        for x in g["geri_alinan"]:
            print("  " + x)
        return 0
    if emir == "paketle":
        if not kalan:
            print("HATA: session_id gerekir (veya 'son' / 'en-uzun')")
            return 2
        sid = kalan[0]
        mod = "eksiksiz" if "--eksiksiz" in kalan[1:] else "akilli"
        jev = "--jev" in kalan[1:]
        # --oto: ayni isi yapan oturumlari SADECE UYARMA, dogrudan ayni pakete al.
        # Karari Jev verir (Jev yoksa yerel esik). --oto-pasif ayrica kaynaklari
        # kapatir+arsivler (geri alinabilir: czip gerial).
        oto = "--oto" in kalan[1:] or "--oto-pasif" in kalan[1:]
        oto_pasif = "--oto-pasif" in kalan[1:]
        birlesenler = []
        kaynak = None
        if oto:
            try:
                import birlestir as _b
                aday = [x["sid"] for x in _b.benzerleri_bul(sid, jev=jev) if x["kabul"]]
            except Exception as _e:
                aday = []
                print("UYARI: oto-birlestirme taramasi basarisiz: %s" % str(_e)[:90])
            if aday:
                birlesenler = [sid] + aday
                print("OTO-BIRLESTIRME: %d oturum tek pakete aliniyor (karar: %s)"
                      % (len(birlesenler), "karar-kapisi" if jev else "yerel"))
                for x in birlesenler:
                    print("   " + x)
                sid_asil = sid
                mesajlar, baslik, rapor = _b.oturumlari_birlestir(birlesenler)
                sid = sid_asil
                print("   %d -> %d ilet (yinelenen atilan: %d)"
                      % (rapor["kaynak_ilet"], rapor["birlesik_ilet"],
                         rapor["yinelenen_atilan"]))
            else:
                print("OTO-BIRLESTIRME: ayni isi yapan baska oturum bulunmadi.")
        if not birlesenler:
            # Bir DOSYA verildiyse oturum kimligi gibi aramadan dogrudan onu paketle.
            # girdi_ayristir zaten JSON / JSONL / duz metin / Hermes export blogu
            # ve mesaj listesi biliyor; tek eksik CLI'ye bagli olmamasiydi.
            _aday = os.path.expanduser(sid)
            if sid.startswith("cc:"):
                # Claude Code / Claude Desktop oturumu (~/.claude/projects/*/*.jsonl)
                import ccd_dokum as _cc
                _y = _cc.coz(sid)
                if not _y:
                    print(T("ERROR: Claude Code session not found: %s  (list: czip cc)",
                            "HATA: Claude Code oturumu bulunamadi: %s  (liste: czip cc)") % sid)
                    return 2
                mesajlar, baslik = _cc.oku(_y)
                baslik = (baslik or os.path.basename(_y))[:60]
                sid = "cc:" + os.path.splitext(os.path.basename(_y))[0]
                _cwd = _cc.calisma_dizini(_y)
                kaynak = {"sid": sid, "cwd": _cwd,
                          "proje": os.path.basename((_cwd or "").rstrip("/"))}
                print(T("CLAUDE SESSION: %s  (%d messages)",
                        "CLAUDE OTURUMU: %s  (%d ileti)") % (_y, len(mesajlar)))
            elif os.path.isfile(_aday):
                mesajlar = girdi_ayristir(_aday)
                if not mesajlar:
                    print(T("ERROR: file is empty or unparseable: %s",
                            "HATA: dosya bos ya da cozulemedi: %s") % _aday)
                    return 2
                baslik = os.path.splitext(os.path.basename(_aday))[0][:60]
                # JSON disa aktarimlarinda gercek baslik govdede olabilir.
                try:
                    _j = json.loads(open(_aday, encoding="utf-8", errors="replace").read())
                    if isinstance(_j, dict):
                        baslik = str(_j.get("title") or _j.get("baslik") or baslik)[:60]
                except Exception:
                    pass
                sid = "dosya:" + os.path.basename(_aday)
                print(T("FILE INPUT: %s  (%d messages)",
                        "DOSYA GIRDISI: %s  (%d ileti)") % (_aday, len(mesajlar)))
            else:
                sid, mesajlar, baslik = oturum_oku(sid)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", (baslik or sid)[:48]).strip("-") or sid
        yol = os.path.join(d, f"{temiz}-{time.strftime('%Y%m%d-%H%M%S')}.hkp")
        kaynak = kaynak or {"sid": sid, "proje": (baslik or "")[:40]}
        r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev, kaynak=kaynak)
        kid = id_ata(r["yol"], baslik or sid)
        import hafiza as _hz
        _hz.kaydet(r, kid, kaynak)
        kirp = len(r.get("eksik_bildirim") or [])
        jv = r.get("jev") or {}
        jtxt = ("" if not jv else
                f"\n  {jv.get('motor', 'karar')}: sorgu={jv.get('sorgu', 0)} sil={jv.get('sil', 0)} "
                f"tut={jv.get('tut', 0)} kirp={jv.get('kirp', 0)} "
                f"sure={jv.get('sure_sn')}sn" + (f" HATA={jv.get('hata')}" if jv.get('hata') else ""))
        print(f"PAKETLENDI: {r['yol']}\n  ID={kid}\n  ilet={r['mesaj']}  {r['kaynak_bayt']}->{r['paket_bayt']}B"
              f"  oran={r['oran']}x  parca={r['parca']}"
              + (f"  kirpilan_arac={kirp}" if kirp else "")
              + (f"  tekrar_ayiklandi={r['tekrar']}(-{r['tekrar_bayt']//1024}KB)" if r.get("tekrar") else "")
              + jtxt
              + f"\n  oku: czip oku {kid}   ara: czip ara {kid} \"sorgu\"")
        if r.get("yon", {}) and r["yon"].get("sonraki"):
            print("  sonraki adim: " + r["yon"]["sonraki"])
        # AYNI ISI YAPAN OTURUM UYARISI — paketledikten sonra, karari Jev verir.
        # --yalniz ile kapatilir (tarama Jev cagrisi yapar, her zaman istenmez).
        if oto and birlesenler and oto_pasif:
            try:
                import birlestir as _b2
                pr = _b2.pasife_al(birlesenler, r["yol"], kid, sebep="czip_oto_merge")
                print("  PASIFE ALINDI: %d oturum (geri al: czip gerial %s)"
                      % (len(pr["pasif"]), os.path.basename(pr["gerial"])))
            except Exception as _e:
                print("  ! PASIFE ALMA BASARISIZ: %s" % str(_e)[:110])
                print("    Paket URETILDI, kaynaklar ACIK kaldi.")
        if ("--yalniz" not in kalan[1:] and not oto
                and not str(sid).startswith(("dosya:", "cc:"))):
            try:
                import birlestir as _b
                benzer = [x for x in _b.benzerleri_bul(sid, jev=jev) if x["kabul"]]
                if benzer:
                    print(f"\n  ! AYNI ISI YAPAN {len(benzer)} OTURUM DAHA VAR"
                          f" (karar: {benzer[0]['kaynak']}):")
                    for x in benzer[:5]:
                        print(f"      {x['sid']}  {x['kaynak']}={x['skor']:.2f}  {x['ozet'][:62]}")
                    print("    Hepsini TEK pakete almak icin:")
                    print("      czip birlestir " + sid + " " + " ".join(x["sid"] for x in benzer[:5]) + " --laya")
                    print("    Birlestirip kaynaklari pasife almak icin:  czip birlestir oto --laya")
            except Exception as _e:
                print(f"    (benzer oturum taramasi atlandi: {str(_e)[:70]})")
        return 0
    if emir == "listele":
        kayit = _kayit_oku()
        if not kayit:
            print("kayit bos — henuz id'li paket yok")
            return 0
        for kid, v in sorted(kayit.items(), key=lambda kv: (kv[1].get("t") or ""), reverse=True):
            print(f"{kid} | {v.get('t','')} | {(v.get('baslik') or '')[:60]}")
        return 0
    if emir == "ayar":
        # czip ayar                 -> show settings
        # czip ayar esik 50         -> offer at %50 of the context window
        # czip ayar kademe 50,75,90 -> escalating tiers
        # czip ayar oto on|off      -> decide automatically instead of asking
        # czip ayar oto-pasif on|off
        import ayar as _a
        if not kalan:
            a = _a.oku()
            print(T("czip settings (%s)", "czip ayarlari (%s)") % _a.AYAR_YOLU)
            print("  esik_oran : %%%d" % round(a["esik_oran"] * 100))
            print("  kademeler : " + ", ".join("%%%d" % round(k * 100) for k in a["kademeler"]))
            print("  oto       : " + ("ON" if a["oto"] else "off")
                  + T("   (on = decide and pack without asking)",
                      "   (on = sormadan karar verip paketler)"))
            print("  oto_pasif : " + ("ON" if a["oto_pasif"] else "off"))
            print("  kalan_tok : %d" % a.get("kalan_token", 0)
                  + T("   (offer when this much room is left; 0=off)",
                      "   (bu kadar yer kalinca teklif; 0=kapali)"))
            print("  mutlak_tok: %d" % a.get("mutlak_token", 0)
                  + T("   (offer at this absolute usage; 0=off)",
                      "   (bu mutlak kullanimda teklif; 0=kapali)"))
            print("  motor     : " + a.get("karar_motoru", "laya")
                  + T("   (decision gate: laya=local, jev=cloud)",
                      "   (karar kapisi: laya=yerel, jev=bulut)"))
            print("  bulut_yed : " + ("ON" if a.get("bulut_yedegi") else "off")
                  + T("   (fall back to cloud Jev if Laya fails — sends samples out)",
                      "   (Laya cokerse bulut Jev'e dus — ornek disari gider)"))
            print("  jev       : " + ("ON" if a.get("jev", True) else "off"))
            print(T("  autopilot (Claude hooks):", "  otopilot (Claude hook'lari):"))
            for ad in ("koruma", "hatirlatma", "brifing", "haftalik_temizlik", "hook_laya"):
                print("    %-17s: %s" % (ad, "ON" if a.get(ad) else "off"))
            print("    claude_ctx       : %d" % a.get("claude_ctx", 200000))
            print("    auto (adim)      : " + ("czip-auto%d" % (a["adim_token"] // 1000)
                                               if a.get("adim_token") else "off"))
            return 0
        anahtar = kalan[0].lower()
        deger = kalan[1] if len(kalan) > 1 else ""
        acik = deger.lower() in ("on", "acik", "1", "true", "evet", "yes")
        try:
            if anahtar in ("esik", "threshold"):
                o = float(deger.rstrip("%")) / (100.0 if float(deger.rstrip("%")) > 1 else 1.0)
                _a.yaz(esik_oran=o)
                print(T("threshold -> %%%d", "esik -> %%%d") % round(o * 100))
            elif anahtar in ("kademe", "tiers"):
                ks = sorted({float(x.strip().rstrip("%")) / 100.0 if float(x.strip().rstrip("%")) > 1
                             else float(x.strip()) for x in deger.split(",") if x.strip()})
                _a.yaz(kademeler=ks)
                print(T("tiers -> %s", "kademeler -> %s")
                      % ", ".join("%%%d" % round(k * 100) for k in ks))
            elif anahtar == "oto":
                _a.yaz(oto=acik)
                print("oto -> " + ("ON" if acik else "off"))
            elif anahtar in ("oto-pasif", "oto_pasif"):
                _a.yaz(oto_pasif=acik)
                print("oto_pasif -> " + ("ON" if acik else "off"))
            elif anahtar in ("motor", "engine"):
                if deger.lower() not in ("laya", "jev"):
                    raise ValueError(deger)
                _a.yaz(karar_motoru=deger.lower())
                print("motor -> " + deger.lower())
            elif anahtar in ("bulut", "bulut_yedegi", "cloud"):
                _a.yaz(bulut_yedegi=acik)
                print("bulut_yedegi -> " + ("ON" if acik else "off"))
            elif anahtar in ("kalan", "kalan_token", "remaining"):
                _a.yaz(kalan_token=max(0, int(float(deger))))
                print("kalan_token -> %d" % max(0, int(float(deger))))
            elif anahtar in ("mutlak", "mutlak_token", "absolute"):
                _a.yaz(mutlak_token=max(0, int(float(deger))))
                print("mutlak_token -> %d" % max(0, int(float(deger))))
            elif anahtar in ("koruma", "hatirlatma", "brifing", "haftalik_temizlik",
                             "hook_laya", "bak_temizle", "guard", "recall", "brief", "cleanup"):
                ad = {"guard": "koruma", "recall": "hatirlatma", "brief": "brifing",
                      "cleanup": "haftalik_temizlik"}.get(anahtar, anahtar)
                _a.yaz(**{ad: acik})
                print("%s -> %s" % (ad, "ON" if acik else "off"))
            elif anahtar in ("ctx", "claude_ctx", "pencere"):
                _a.yaz(claude_ctx=max(1000, int(float(deger))))
                print("claude_ctx -> %d" % max(1000, int(float(deger))))
            elif anahtar == "jev":
                _a.yaz(jev=acik)
                print("jev -> " + ("ON" if acik else "off"))
            else:
                print(T("unknown setting: %s", "bilinmeyen ayar: %s") % anahtar)
                return 2
        except ValueError:
            print(T("bad value: %r", "gecersiz deger: %r") % deger)
            return 2
        return 0
    if emir == "index":
        # Build/refresh the one searchable store over every package.
        import depo as _d
        tam = "--full" in kalan or "--tam" in kalan
        print(T("Indexing packages%s ...", "Paketler indeksleniyor%s ...")
              % (" (full rebuild)" if tam else ""))
        r = _d.guncelle(tam=tam)
        print(T("  new=%d updated=%d unchanged=%d  messages=%d",
                "  yeni=%d guncellenen=%d degismeyen=%d  ileti=%d")
              % (r["yeni"], r["guncellenen"], r["atlanan"], r["ileti"]))
        print(T("  store: %s  (%.1f MB)", "  depo: %s  (%.1f MB)")
              % (r["depo"], r["boyut"] / 1e6))
        st = _d.istatistik()
        if st:
            print(T("  searchable: %d packages / %d messages",
                    "  aranabilir: %d paket / %d ileti")
                  % (st["packages"], st["indexed"]))
        return 0
    if emir == "arsivara":
        # Fast path: use the index when it exists; fall back to scanning packages.
        try:
            import depo as _d
            if os.path.exists(_d.yol()) and kalan and "--tara" not in kalan:
                r = _d.ara(kalan[0], limit=25)
                print(T("STORE SEARCH %r — %d hits", "DEPO ARAMA %r — %d isabet")
                      % (kalan[0], r["total"]))
                for pk in r["packages"]:
                    ad = pk["kid"] or os.path.basename(pk["path"])
                    print("\n%s  [%s]  %s" % (
                        ad, time.strftime("%d.%m %H:%M", time.localtime(pk["mtime"])),
                        str(pk["title"])[:60]))
                    for h in pk["hits"]:
                        print("   #%s %s: %s" % (h["i"], h["role"], h["snippet"][:150]))
                    if pk["kid"]:
                        print("   -> czip range %s <i-i>" % pk["kid"])
                if not r["packages"]:
                    print(T("(no hits; `czip index` may be stale)",
                            "(isabet yok; `czip index` bayat olabilir)"))
                return 0
        except Exception as _e:
            print(T("(index unavailable, scanning packages: %s)",
                    "(indeks yok, paketler taraniyor: %s)") % str(_e)[:70])

        # TUM paketlerde arama: paket dizini = uzun sureli aranabilir bellek.
        # Tek tek paket acmak yerine "bu isi nerede yapmistim?" sorusunu cevaplar.
        if not kalan:
            print("HATA: kullanim -> czip arsivara \"<sorgu>\" [--paket=N] [--satir=M]")
            return 2
        sorgu = kalan[0]
        pmax = 40
        smax = 2
        for a in kalan[1:]:
            if a.startswith("--paket="):
                pmax = max(1, int(a.split("=", 1)[1]))
            elif a.startswith("--satir="):
                smax = max(1, int(a.split("=", 1)[1]))
        d = os.path.expanduser(PAKET_DIZIN)
        if not os.path.isdir(d):
            print("Paket dizini yok:", d)
            return 0
        paketler = sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".hkp")),
                          key=os.path.getmtime, reverse=True)[:pmax]
        kayit = _kayit_oku()
        yol2id = {}
        for k, v in (kayit.items() if isinstance(kayit, dict) else []):
            y = v.get("yol") if isinstance(v, dict) else v
            if y:
                yol2id[os.path.abspath(os.path.expanduser(str(y)))] = k
        toplam = 0
        bulunan_paket = 0
        print("ARSIV ARAMA %r — %d pakette" % (sorgu, len(paketler)))
        for yol in paketler:
            try:
                r = paket_ara(yol, sorgu, max_satir=smax)
            except Exception:
                continue
            son = r if isinstance(r, list) else (r.get("sonuclar") or [])
            if not son:
                continue
            bulunan_paket += 1
            toplam += len(son)
            kid = yol2id.get(os.path.abspath(yol))
            ad = kid or os.path.basename(yol)
            zaman = time.strftime("%d.%m %H:%M", time.localtime(os.path.getmtime(yol)))
            print("\n%s  [%s]  %d isabet" % (ad, zaman, len(son)))
            for x in son[:smax]:
                es = " ".join(str(x.get("eslesme", "")).split())
                print("   #%s %s: %s" % (x.get("i"), x.get("rol", "?"), es[:150]))
            if kid:
                print("   -> czip aralik %s <i-i>" % kid)
        print("\nTOPLAM: %d isabet / %d paket" % (toplam, bulunan_paket))
        if not toplam:
            print("(hicbir pakette bulunamadi)")
        return 0
    if emir == "ara":
        if len(kalan) < 2:
            print("HATA: kullanim -> czip ara <id|son> \"sorgu\"")
            return 2
        yol = id_coz(kalan[0])
        if not yol or not os.path.exists(yol):
            print("HATA: paket bulunamadi:", kalan[0])
            return 2
        try:
            sonuclar = paket_ara(yol, " ".join(kalan[1:]))
        except ValueError as e:
            print("HATA:", e)
            return 2
        print(json.dumps({"paket": os.path.basename(yol), "bulunan": len(sonuclar),
                          "sonuclar": sonuclar}, ensure_ascii=False))
        print("IPUCU: ilgili i icin tam metin -> czip aralik %s \"i\" (veya i-i2)" % kalan[0])
        return 0
    if emir == "oku":
        girdi = kalan[0] if kalan else None
        yol = id_coz(girdi)
        if not yol or not os.path.exists(yol):
            print("HATA: paket yok — once: czip paketle <id>  (id veya 'son' ver)")
            return 2
        g = kilavuz(yol)
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
    if emir == "harita":
        # RAG-oncelikli okuma: tam indeks YERINE ince harita (~1.5k token).
        yol = id_coz(kalan[0]) if kalan else son_paket()
        if not yol or not os.path.exists(yol):
            print("HATA: paket bulunamadi:", kalan[0] if kalan else "(son)")
            return 2
        h = harita(yol)
        out = []
        out.append("MAP %s | %d msgs | roles=%s | dedup=%d"
              % (h["baslik"], h["toplam"],
                 ",".join("%s:%d" % kv for kv in sorted(h["roller"].items())),
                 h["tekrar_isaretli"]))
        if h["araclar"]:
            out.append("TOOLS " + " ".join("%s:%d" % kv for kv in h["araclar"]))
        if h.get("yon"):
            import yon as _yon
            _kid = kalan[0] if kalan and len(kalan[0]) == 6 else None
            out.append(_yon.metin(h["yon"], _kid))
        out.append("REQUESTS %d total, last %d:" % (h["istek_toplam"], len(h["istekler"])))
        for i, c in h["istekler"]:
            out.append("  %d| %s" % (i, c))
        # TAIL kompakt gosterim: JSON sarmalayicisi (alan adlari, tirnak, kacis)
        # olculdu -> son iletlerin %47'si sirf tören. Rol tek harfe iner,
        # icerik duz metin kalir. Okuyan AI icin kayip yok.
        out.append("TAIL (%d full, R>=role: U=user A=assistant T=tool S=system):" % len(h["son"]))
        for it in h["son"]:
            rol = str(it.get("rol") or it.get("r") or "?")[:1].upper()
            ic = it.get("icerik")
            if ic is None:
                ic = it.get("c") or ""
            if not isinstance(ic, str):
                ic = json.dumps(ic, ensure_ascii=False, separators=(",", ":"))
            ic = " ".join(ic.split())
            ek = ""
            tc = it.get("arac") or it.get("tc")
            if tc:
                adlar = [x.get("n", "?") if isinstance(x, dict) else str(x) for x in tc] \
                        if isinstance(tc, list) else [str(tc)]
                ek = "[" + ",".join(adlar) + "]"
            out.append("%s>%s %s" % (rol, ek, ic[:700]))
        out.append("HOW " + h["talimat"])
        metin = "\n".join(out)
        print(metin)
        # Token ekonomisi: kullanici degeri GORSUN (tahmin: ~4 karakter/token).
        harita_tok = max(1, len(metin) // 4)
        tam_tok = max(1, h["tam_karakter"] // 4)
        print("COST map~%s tok vs full~%s tok -> %d%% saved (%.0fx)" % (
            _k_fmt(harita_tok), _k_fmt(tam_tok),
            max(0, round(100 * (1 - harita_tok / tam_tok))), tam_tok / harita_tok))
        return 0
    if emir == "yon":
        yol = id_coz(kalan[0]) if kalan else son_paket()
        if not yol or not os.path.exists(yol):
            print("HATA: paket bulunamadi:", kalan[0] if kalan else "(son)")
            return 2
        import yon as _yon
        meta = meta_oku(yol)
        k = meta.get("yon")
        if not k:  # eski paket: kart yok -> simdi cikar (paketi degistirmeden)
            _, kayitlar = yukle(yol)
            soz = meta.get("soz", [])
            kayitlar = [dict(x, c=coz_sozluk(x["c"], soz)) if isinstance(x.get("c"), str) else x
                        for x in kayitlar]
            k = _yon.kart(kayitlar, meta.get("baslik"))
        print(_yon.metin(k, kalan[0] if kalan and len(kalan[0]) == 6 else None,
                         (meta.get("baslik") or "")[:60]))
        return 0
    if emir == "gunluk":
        import hafiza as _hz
        n = 10
        for a in kalan:
            if a.startswith("--n="):
                n = max(1, int(a.split("=", 1)[1]))
        liste = _hz.gunluk(n=n)
        if not liste:
            print(T("journal is empty — pack something first", "gunluk bos — once paketle"))
            return 0
        for o in liste:
            print("%s  %s  [%s] %s  (%s ileti)" % (o["t"], o.get("kid") or "?",
                  (o.get("proje") or "")[:20], (o.get("baslik") or "")[:50], o.get("ileti")))
            if o.get("sonraki"):
                print("      sonraki: " + o["sonraki"][:120])
        return 0
    if emir == "brifing":
        import hafiza as _hz
        cwd = None
        for a in kalan:
            if a.startswith("--cwd="):
                cwd = os.path.abspath(os.path.expanduser(a.split("=", 1)[1]))
        print(_hz.brifing(cwd=cwd, n=5) or T("(nothing recorded yet)", "(henuz kayit yok)"))
        return 0
    if emir == "hatirla":
        if not kalan:
            print("HATA: kullanim -> czip hatirla \"istek metni\"")
            return 2
        import hafiza as _hz
        metin, _ = _hz.hatirlatma(" ".join(kalan), limit=5)
        print(metin or T("(nothing relevant — no recall is better than a wrong one)",
                         "(ilgili gecmis yok — yanlis hatirlatmaktansa hic)"))
        return 0
    if emir == "temizle":
        import temizlik as _t
        # English aliases: --apply / --quiet / undo|restore
        kalan = [{"--apply": "--uygula", "--quiet": "--sessiz", "undo": "geri",
                  "restore": "geri"}.get(x, x) for x in kalan]
        if kalan and kalan[0] == "geri":
            n = _t.geri_al(kalan[1] if len(kalan) > 1 else None)
            print(T("restored %d items", "%d oge geri yuklendi") % n)
            return 0
        sessiz = "--sessiz" in kalan
        islemler = _t.plan()
        if "--uygula" not in kalan:
            print(T("CLEANUP PLAN (dry run): ", "TEMIZLIK PLANI (deneme): ") + _t.ozet(islemler))
            for x in islemler[:40]:
                print("  %-11s %7.1f KB  %s%s" % (x["neden"], x.get("bayt", 0) / 1024,
                      os.path.basename(x["yol"]),
                      ("  -> " + os.path.basename(x["yonlendir"])) if x.get("yonlendir") else ""))
            print(T("apply: czip clean --apply   (moves to trash; undo: czip clean geri)",
                    "uygula: czip temizle --uygula   (cope tasir; geri: czip temizle geri)"))
            return 0
        r = _t.uygula(islemler)
        if not sessiz:
            print(T("CLEANED: ", "TEMIZLENDI: ") + _t.ozet(islemler))
            print("  cop: %s   bosaltilan eski cop: %d   budanan kayit: %d" % (
                r["cop"] or "-", r["bosaltilan"], r["kayit_budanan"]))
            if r["cop"]:
                print(T("  undo: czip clean geri", "  geri al: czip temizle geri"))
        return 0
    if emir == "hook":
        import claude_hook as _h
        try:
            veri = json.load(sys.stdin)
        except Exception:
            veri = {}
        cikti = _h.calistir(kalan[0] if kalan else veri.get("hook_event_name", ""), veri)
        if cikti:
            print(cikti)
        return 0
    if emir == "cc":
        import ccd_dokum as _cc
        n = 10
        for a in kalan:
            if a.startswith("--n="):
                n = max(1, int(a.split("=", 1)[1]))
        liste = _cc.oturumlar(limit=n)
        if not liste:
            print(T("No Claude Code sessions under %s", "Claude Code oturumu yok: %s")
                  % _cc.PROJE_DIZIN)
            return 0
        for y, mt, b in liste:
            print("cc:%s | %s | %6.1f KB | %s" % (
                os.path.splitext(os.path.basename(y))[0][:8],
                time.strftime("%d.%m %H:%M", time.localtime(mt)), b / 1024,
                os.path.basename(os.path.dirname(y))[:40]))
        print(T("pack: czip pack cc:<id>   (cc:last = newest)",
                "paketle: czip paketle cc:<id>   (cc:son = en yeni)"))
        return 0
    if emir == "cevre":
        # Aramadan bulunan i'nin etrafini oku: aralik yazmakla ugrasma.
        if len(kalan) < 2:
            print("HATA: kullanim -> czip cevre <id|son> <i> [--n=3]")
            return 2
        yol = id_coz(kalan[0])
        if not yol or not os.path.exists(yol):
            print("HATA: paket bulunamadi:", kalan[0])
            return 2
        n = 3
        for a in kalan[2:]:
            if a.startswith("--n="):
                n = max(0, int(a.split("=", 1)[1]))
        try:
            i = int(kalan[1])
            _, kayitlar = yukle(yol)
            bas, son = max(0, i - n), min(len(kayitlar) - 1, i + n)
            iletler = mesaj_araligi(yol, "%d-%d" % (bas, son))
        except (ValueError, IndexError) as e:
            print("HATA:", e)
            return 2
        for j, it in enumerate(iletler, bas):
            print(("=> " if j == i else "   ") + json.dumps(dict(i=j, **it), ensure_ascii=False))
        return 0
    if emir == "aralik":
        if len(kalan) < 2:
            print("HATA: kullanim -> czip aralik <id|son> <bas-bit>")
            return 2
        yol = id_coz(kalan[0])
        if not yol or not os.path.exists(yol):
            print("HATA: paket bulunamadi:", kalan[0])
            return 2
        try:
            iletler = mesaj_araligi(yol, kalan[1])
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
