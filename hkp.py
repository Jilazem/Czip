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


# ------------------------------------------------------- Jev karar kapisi (opsiyonel)
# Arac ciktilarini tek tek 'gerekiyor mu?' diye Jev'e (System-One) TOPLU sorar:
# olu olanlar pakete hic girmez, gerekenler OLDUGU GIBI kalir (ozet yok).
# Hata/timeout -> sessiz fallback (statik dilim). state.db'ye asla dokunulmaz;
# 'silme' yalniz paketten cikarmadir, kaynak durur.
JEV_API = "https://api.typesafe.ai/v1/systemone"
JEV_TOPLU = 20       # tek istekteki soru sayisi (batch ~9x tasarruf)
JEV_AJ = 0.30        # altindaki noul -> olu sayilir, paketten cikarilir
JEV_TUT = 0.55       # ustundeki noul -> tam kalir, statik dilim uygulanmaz
JEV_MAX = 240        # oturum basina degerlendirilecek en fazla arac ciktilari
JEV_MIN_BAYT = 1200  # altindaki kucuk ciktilar sorgulanmaz (kazanctan dusuk)
JEV_ONEK = 1400      # soruya gidecek ornek: bas dilimi
JEV_SON = 300        # soruya gidecek ornek: son dilim
JEV_BUTCE_SN = 90    # toplam ag butcesi — asilirsa kalan statik dilime doner
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


def jev_kararlar(kayitlar, baslik, son_kullanici, esik_at=JEV_AJ, esik_tut=JEV_TUT,
                 timeout=25):
    """Buyuk arac ciktilarini Jev'e toplu sorar.
    Doner: (karar: {idx: (eylem, noul)}, bilgi: dict)
      eylem: 'sil' | 'tut' | 'kirp'  — hata durumunda karar bos, bilgi['hata'] dolu."""
    karar, bilgi = {}, {}
    key = _jev_anahtar()
    if not key:
        return karar, {"hata": "anahtar_yok"}
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
            cevap = _jev_batch(key, durum, sorular, timeout)
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
             baslik=None, kaynak_bayt=None, jev=False):
    """Mesaj listesi -> .hkp paketi. Sozluk doner (hata durumunda 'hata' anahtari).
    jev=True: 'akilli' modda buyuk arac ciktilarini Jev'e toplu sor; olu olanlar
    paketten cikarilir (iz birakarak), gerekenler OLDUGU GIBI kalir, ortalar statik
    dilime duser. Jev'e ulasilamazsa mevcut statik dilim davranisina doner."""
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
        kararlar, jev_bilgi = jev_kararlar(kayitlar, baslik, son_kul)
    for i, k in enumerate(kayitlar):
        c = k.get("c")
        if k.get("r") != "tool" or not isinstance(c, str):
            continue
        eylem = kararlar.get(i, (None, None))[0]
        if eylem == "sil":
            noul = kararlar[i][1]
            k["c"] = f"[JEV-SILINDI noul={noul} — kaynak girdide duruyor]"
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
        if c.startswith("[JEV-SILINDI") or c.startswith("[AYNI-#"):
            continue
        h = hashlib.blake2b(c.encode("utf-8", "replace"), digest_size=16).digest()
        ilk = ilk_gorulen.get(h)
        if ilk is None:
            ilk_gorulen[h] = i
            continue
        k["c"] = f"[AYNI-#{ilk} — bu arac ciktisi {ilk} numarali iletle birebir ayni; gerekirse orayi oku]"
        tekrar_sayisi += 1
        tekrar_bayt += len(c) - len(k["c"])

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
            "tekrar": tekrar_sayisi, "tekrar_bayt": tekrar_bayt,
            "jev": jev_bilgi,
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
    return {
        "baslik": meta.get("b") or os.path.basename(dosya),
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


# ------------------------------------------------------------- CLI
def _main(argv):
    """czip komutlari:
      czip paketle <session_id|son|en-uzun> [--eksiksiz] [--jev]   -> oturumu paketle
      czip oku <paket.hkp|son>                             -> indeks + son 6 + durum
      czip aralik <paket.hkp|son> <bas-bit>                -> secili araligi tam oku
    --jev: akilli modda buyuk arac ciktilarini Jev'e sor; oluler paketten cikar.
    """
    if not argv or argv[0] in ("-h", "--help", "yardim"):
        print("Kullanim:\n"
              "  czip paketle <session_id|son|en-uzun> [--eksiksiz] [--jev]\n"
              "                 [--oto] ayni isi yapanlari da ayni pakete al\n"
              "                 [--oto-pasif] ayrica kaynaklari kapat+arsivle\n"
              "  czip oku <paket.hkp|son>\n"
              "  czip aralik <paket.hkp|son> <bas-bit>\n"
              "  czip birlestir [oto|<id1> <id2> ...] [--jev] [--gun=7] [--pasif-yok]\n"
              "  czip harita <id|son>    -> RAG haritasi (~1.5k token; oku'nun ucuz hali)\n"
              "  czip gerial [<dosya>]   -> pasife alinan oturumlari geri ac")
        return 0
    emir, kalan = argv[0], argv[1:]
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
                  + (f"  | Jev: {jb.get('hata') or str(jb.get('cevap')) + ' cevap'}" if jev else ""))
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
                      + (" --jev" if jev else ""))
                return 0
            idler = gruplar[0]
            print("\noto: 1. grup birlestiriliyor...")
        mesajlar, baslik, rapor = _b.oturumlari_birlestir(idler)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", baslik[:48]).strip("-") or "birlesik"
        yol = os.path.join(d, f"BIRLESIK-{temiz}-{time.strftime('%Y%m%d-%H%M%S')}.hkp")
        r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev)
        kid = id_ata(r["yol"], baslik)
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
                print("  Kaynaklari pasife de almak icin: czip birlestir oto --jev")
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
                      % (len(birlesenler), "jev" if jev else "yerel"))
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
            sid, mesajlar, baslik = oturum_oku(sid)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", (baslik or sid)[:48]).strip("-") or sid
        yol = os.path.join(d, f"{temiz}-{time.strftime('%Y%m%d-%H%M%S')}.hkp")
        r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev)
        kid = id_ata(r["yol"], baslik or sid)
        kirp = len(r.get("eksik_bildirim") or [])
        jv = r.get("jev") or {}
        jtxt = ("" if not jv else
                f"\n  jev: sorgu={jv.get('sorgu', 0)} sil={jv.get('sil', 0)} "
                f"tut={jv.get('tut', 0)} kirp={jv.get('kirp', 0)} "
                f"sure={jv.get('sure_sn')}sn" + (f" HATA={jv.get('hata')}" if jv.get('hata') else ""))
        print(f"PAKETLENDI: {r['yol']}\n  ID={kid}\n  ilet={r['mesaj']}  {r['kaynak_bayt']}->{r['paket_bayt']}B"
              f"  oran={r['oran']}x  parca={r['parca']}"
              + (f"  kirpilan_arac={kirp}" if kirp else "")
              + (f"  tekrar_ayiklandi={r['tekrar']}(-{r['tekrar_bayt']//1024}KB)" if r.get("tekrar") else "")
              + jtxt
              + f"\n  oku: czip oku {kid}   ara: czip ara {kid} \"sorgu\"")
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
        if "--yalniz" not in kalan[1:] and not oto:
            try:
                import birlestir as _b
                benzer = [x for x in _b.benzerleri_bul(sid, jev=jev) if x["kabul"]]
                if benzer:
                    print(f"\n  ! AYNI ISI YAPAN {len(benzer)} OTURUM DAHA VAR"
                          f" (karar: {benzer[0]['kaynak']}):")
                    for x in benzer[:5]:
                        print(f"      {x['sid']}  {x['kaynak']}={x['skor']:.2f}  {x['ozet'][:62]}")
                    print("    Hepsini TEK pakete almak icin:")
                    print("      czip birlestir " + sid + " " + " ".join(x["sid"] for x in benzer[:5]) + " --jev")
                    print("    Birlestirip kaynaklari pasife almak icin:  czip birlestir oto --jev")
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
        print("MAP %s | %d msgs | roles=%s | dedup=%d"
              % (h["baslik"], h["toplam"],
                 ",".join("%s:%d" % kv for kv in sorted(h["roller"].items())),
                 h["tekrar_isaretli"]))
        if h["araclar"]:
            print("TOOLS " + " ".join("%s:%d" % kv for kv in h["araclar"]))
        print("REQUESTS %d total, last %d:" % (h["istek_toplam"], len(h["istekler"])))
        for i, c in h["istekler"]:
            print("  %d| %s" % (i, c))
        # TAIL kompakt gosterim: JSON sarmalayicisi (alan adlari, tirnak, kacis)
        # olculdu -> son iletlerin %47'si sirf tören. Rol tek harfe iner,
        # icerik duz metin kalir. Okuyan AI icin kayip yok.
        print("TAIL (%d full, R>=role: U=user A=assistant T=tool S=system):" % len(h["son"]))
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
            print("%s>%s %s" % (rol, ek, ic[:700]))
        print("HOW " + h["talimat"])
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
