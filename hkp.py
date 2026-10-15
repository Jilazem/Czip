# -*- coding: utf-8 -*-
"""hkp — HKP1 paket motoru (saf, MCP'siz).

oturum-sikistirici MCP sunucusu ve /czip plugin'i TARAFINDAN ORTAK kullanilir.
Amaç: uzun bir Hermes oturumunu yeni oturuma tasinacak kadar küçültmek; okuyan AI
eksiksiz anlayip devam edebilsin. Kayip yok: 'eksiksiz' modda her bayt geri doner
(bu modda arac-ciktisi kirpma, Clef budaması ve tekrar ayiklama DEVRE DISI).

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
# Also support explicit importlib loading by external benchmark adapters.
_engine_dir = os.path.dirname(os.path.abspath(__file__))
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)
from czip_runtime import T, atomic_json, file_lock, owner_key, read_json, session_env, session_identity, unique_package_path

IMZA = b"HKP1"
V = 1
LZMA_SABIT = {"preset": 9 | lzma.PRESET_EXTREME}
JETON_AC = "\u241f"      # ␟
JETON_KAP = "\u241e"     # ␞
JETON_RE = re.compile("\u241f(\\d{1,6})\u241e")
KOD_IPUCU = re.compile(r"(<[a-zA-Z/!]|function |=>|\bdef \b|\bimport \b|```\|\|.*\||=>|\{\{|\}\})")
PARCA_ILET = 40
PAKET_DIZIN = os.environ.get("CZIP_PAKET_DIZIN", os.path.join(session_env("HERMES_HOME", "~/.hermes"), "session-packs"))
KAYIT_YOL = os.environ.get("CZIP_KAYIT_YOL", os.path.join(PAKET_DIZIN, "kayit.json"))
ARAMA_MAX_SATIR = 8


def _kayit_oku():
    """A missing registry is empty; a damaged registry must not be overwritten."""
    value = read_json(KAYIT_YOL, {})
    if not isinstance(value, dict):
        raise ValueError(T("Invalid package registry", "Geçersiz paket kaydı"))
    return value


def _kayit_yaz(kayit):
    atomic_json(KAYIT_YOL, kayit)


def id_ata(yol, baslik=None, source_id=None):
    with file_lock(KAYIT_YOL):
        return _id_ata(yol, baslik, source_id)


def _id_ata(yol, baslik=None, source_id=None):
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
            kayit[kid] = {"yol": yol, "baslik": (baslik or "")[:120], "t": time.strftime("%Y-%m-%d %H:%M"),
                          "created_ns": time.time_ns(), "owner": owner_key(), "source_id":source_id, "source_database":os.path.realpath(db_yolu())}
            _kayit_yaz(kayit)
            return kid
    raise RuntimeError("id havuzu tukendi")


def id_coz(girdi):
    """'son', 6 haneli id veya yol -> paket yolu (yoksa None)."""
    if not girdi or girdi in ("son", "last", "latest"):
        return son_paket()
    kayit = _kayit_oku()
    if girdi in kayit:
        value = kayit[girdi]
        return value.get("yol") if isinstance(value, dict) else value
    return os.path.abspath(os.path.expanduser(girdi))


def verify_package_source(kid, path, sid):
    """Bind a handover to its exact source and profile before any session reset."""
    entry = _kayit_oku().get(kid)
    if (not isinstance(entry, dict) or entry.get("source_id") != sid or
        entry.get("source_database") != os.path.realpath(db_yolu()) or
        os.path.realpath(entry.get("yol", "")) != os.path.realpath(path)):
        raise ValueError(T("Package/source/profile identity mismatch", "Paket/kaynak/profil kimliği eşleşmiyor"))
    if harita(path, son_n=0, istek_max=0)["toplam"] < 1:
        raise ValueError(T("Package is empty", "Paket boş"))
    return {"ok":True, "source_id":sid, "kid":kid}


def hata(msg):
    return json.dumps({"ok": False, "hata": str(msg)}, ensure_ascii=False)


# ------------------------------------------------------------- girdi ayristirma
def _claude_code_kayit_mi(k: dict) -> bool:
    """Claude Code JSONL satiri mi? (message.role var, ust duzeyde role/content yok)"""
    return (isinstance(k, dict) and not k.get("role") and isinstance(k.get("message"), dict)
            and bool(k["message"].get("role")))


def _blok_metni(blok) -> str:
    """Claude Code icerik blogunu okunur metne cevir (text / tool_use / tool_result)."""
    if isinstance(blok, str):
        return blok
    if not isinstance(blok, dict):
        return ""
    t = blok.get("type")
    if t == "text":
        return str(blok.get("text") or "")
    if t == "thinking":
        return "[dusunme] " + str(blok.get("thinking") or "")[:4000]
    if t == "tool_use":
        girdi = blok.get("input")
        try:
            girdi_m = json.dumps(girdi, ensure_ascii=False)
        except Exception:
            girdi_m = str(girdi)
        return f"[arac: {blok.get('name')}] {girdi_m[:4000]}"
    if t == "tool_result":
        ic = blok.get("content")
        if isinstance(ic, list):
            return "[arac sonucu] " + " ".join(_blok_metni(b) for b in ic)[:8000]
        return "[arac sonucu] " + str(ic or "")[:8000]
    if t == "image":
        return "[gorsel]"
    return ""


def claude_code_cevir(kayitlar: list) -> list:
    """Claude Code JSONL -> czip mesaj listesi ({r: rol, c: icerik}). Meta satirlari atlanir."""
    out = []
    for k in kayitlar:
        if not _claude_code_kayit_mi(k):
            continue
        m = k["message"]
        rol = str(m.get("role") or "")
        ic = m.get("content")
        if isinstance(ic, list):
            metin = "\n".join(x for x in (_blok_metni(b) for b in ic) if x)
        else:
            metin = str(ic or "")
        if not metin.strip():
            continue
        # arac sonuclari kullanici rolunde gelir; czip icin 'tool' olarak isaretle
        if rol == "user" and metin.startswith("[arac sonucu]"):
            rol = "tool"
        out.append({"role": rol, "content": metin,
                    "timestamp": k.get("timestamp"), "uuid": k.get("uuid")})
    return out


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
    if len(girdi) < 2048 and "\n" not in girdi and os.path.isfile(girdi):
        import ajanlar
        if ajanlar.bicim_algila(girdi) == "codex":
            return ajanlar.oku(girdi)[2]
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
                # Claude Code JSONL: role/content 'message' icinde, icerik blok dizisi.
                # Cevrilmezse paket BOS cikar (23.09.2026: 4998 ilet -> 14 KB, arama 0 sonuc).
                if sum(1 for k in kayitlar[:50] if _claude_code_kayit_mi(k)) >= 3:
                    cevrilmis = claude_code_cevir(kayitlar)
                    if cevrilmis:
                        return cevrilmis
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


def kayit_yap(m, sabit_adaylari, eksiksiz=False):
    """Tek ham mesaj -> kanonik kisa kayit.
    api_content atilir: icerik + oturuma-ozel memory-context enjeksiyonudur,
    yeni oturumda kendiliginden yenilenir."""
    rol = str(m.get("role") or "?")
    icerik = m.get("content") if eksiksiz else json_metin_ayristir(m.get("content"))
    if icerik is not None and not isinstance(icerik, str):
        icerik = json.dumps(icerik, ensure_ascii=False, separators=(",", ":"))
    k = {"r": rol}
    if icerik or (eksiksiz and icerik is not None):
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
    if not eksiksiz and isinstance(r2, str) and r2 == r1:
        r2 = None
    if isinstance(r1, str) and r1:
        k["e"] = r1
    if isinstance(r2, str) and r2:
        k["e2"] = r2
    fr = m.get("finish_reason")
    if fr and (eksiksiz or fr != "stop"):
        k["f"] = fr
    x = {}
    for a in ("id", "tool_call_id", "tool_name", "platform_message_id",
              "timestamp", "token_count", "observed", "compacted"):
        v = m.get(a)
        if v is None or v == "" or v is False or v == []:
            continue
        x[a] = v
    if eksiksiz:
        for field, value in m.items():
            if field not in ("role", "content", "tool_calls", "reasoning", "reasoning_content", "finish_reason"):
                x[field] = value
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


# ------------------------------------------------------- Clef karar kapısı (opsiyonel)
# Arac ciktilarini tek tek 'gerekiyor mu?' diye Clef'e TOPLU sorar:
# olu olanlar pakete hic girmez, gerekenler OLDUGU GIBI kalir (ozet yok).
# Hata/timeout -> sessiz fallback (statik dilim). state.db'ye asla dokunulmaz;
# 'silme' yalniz paketten cikarmadir, kaynak durur.
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


# Optional local decision gate. Legacy ``jev`` Python arguments remain aliases.
KARAR_MOTORU = os.environ.get("CZIP_KARAR_MOTORU", "clef").strip().lower()
KARAR_MIN_GUVEN = .30
from contextvars import ContextVar
_karar_confidence = ContextVar("czip_decision_confidence", default=None)


def karar_guven():
    return _karar_confidence.get() or {}


def karar_batch(durum, sorular, timeout=25, motor=None):
    """Run an explicitly enabled local Clef gate with bounded, isolated I/O."""
    import subprocess
    import math
    if (motor or KARAR_MOTORU) != "clef":
        raise ValueError(T("Only the local Clef decision gate is supported", "Yalnız yerel Clef karar kapısı destekleniyor"))
    configured = os.environ.get("CZIP_CLEF_COMMAND")
    command = json.loads(configured) if configured else [os.environ.get("CZIP_CLEF_PYTHON", sys.executable),
               os.path.join(os.path.dirname(os.path.abspath(__file__)), "clef_kapi.py")]
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("CZIP_CLEF_COMMAND must be a JSON argument array")
    _karar_confidence.set({})
    try:
        response = subprocess.run(command, input=json.dumps({"state": durum, "questions": sorular}, ensure_ascii=False)+"\n",
                                  capture_output=True, text=True, timeout=timeout, check=True)
        data = json.loads(response.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("clef_unavailable:" + type(error).__name__) from None
    if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("answers"), dict):
        raise ValueError("clef_invalid_response")
    if set(data["answers"]) != set(sorular):
        raise ValueError("clef_unknown_question_or_incomplete_answers")
    if not isinstance(data.get("confidence", {}), dict):
        raise ValueError("clef_invalid_confidence")
    answers = {}
    confidence = {}
    for key, value in data["answers"].items():
        if key not in sorular:
            raise ValueError("clef_unknown_question")
        try:
            if isinstance(value, bool): raise ValueError()
            score = float(value)
            certainty = float((data.get("confidence") or {}).get(key, .5))
        except (TypeError, ValueError):
            raise ValueError("clef_invalid_score") from None
        if not math.isfinite(score) or not 0 <= score <= 1 or not math.isfinite(certainty) or not 0 <= certainty <= 1:
            raise ValueError("clef_invalid_score")
        answers[key] = score; confidence[key] = certainty
    _karar_confidence.set(confidence)
    return answers, "clef"


def karar_esikleri(motor=None):
    return .15, JEV_TUT


def jev_kararlar(kayitlar, baslik, son_kullanici, esik_at=JEV_AJ, esik_tut=JEV_TUT,
                 timeout=25):
    """Buyuk arac ciktilarini Clef'e toplu sorar.
    Doner: (karar: {idx: (eylem, noul)}, bilgi: dict)
      eylem: 'sil' | 'tut' | 'kirp'  — hata durumunda karar bos, bilgi['hata'] dolu."""
    karar, bilgi = {}, {"motor": KARAR_MOTORU}
    if esik_at == JEV_AJ:
        esik_at, esik_tut = karar_esikleri()
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
        _karar_confidence.set({})
        try:
            cevap, bilgi["motor"] = karar_batch(durum, sorular, timeout)
        except ValueError as e:
            bilgi["hata"] = str(e)
            break
        for q, n in cevap.items():
            i = qmap.get(q)
            if i is None:
                continue
            g = karar_guven().get(q)
            if g is not None and g < KARAR_MIN_GUVEN:
                # dusuk guvenli karar: silme/kirpma yonunde kullanilmaz, icerik korunur
                karar[i] = ("tut", round(n, 3))
                bilgi["dusuk_guven"] = bilgi.get("dusuk_guven", 0) + 1
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
    import tempfile
    parent = os.path.dirname(os.path.abspath(yol))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".czip-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(IMZA)
            f.write(struct.pack("<II", len(mbayt), len(vbayt)))
            f.write(mbayt); f.write(vbayt)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary, yol)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def paket_oku(yol):
    with open(yol, "rb") as f:
        ham = f.read()
    if not ham.startswith(IMZA):
        raise ValueError("HKP1 imzasi yok — bu aracla uretilmis bir dosya degil")
    mu, vu = struct.unpack("<II", ham[4:12])
    meta = json.loads(lzma.decompress(ham[12:12 + mu]))
    veri = lzma.decompress(ham[12 + mu:12 + mu + vu]).decode("utf-8")
    return meta, veri


def coz_sozluk(s, sozluk):
    """Jetonlari sozlukte cozer."""
    if not isinstance(s, str) or not s:
        return s
    for _ in range(8):
        def _coz(m):
            j = int(m.group(1))
            # Bozuk/kismen-yazilmis pakette indeks tasabilir — tokeni aynen birak.
            return sozluk[j] if j < len(sozluk) else m.group(0)
        y = JETON_RE.sub(_coz, s)
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
def sikistir(mesajlar, cikti, mod="eksiksiz", arac_bas=2000, arac_son=2000,
             baslik=None, kaynak_bayt=None, jev=False):
    """Mesaj listesi -> .hkp paketi. Sozluk doner (hata durumunda 'hata' anahtari).
    jev=True: 'akilli' modda buyuk arac ciktilarini Clef'e toplu sor; olu olanlar
    paketten cikarilir (iz birakarak), gerekenler OLDUGU GIBI kalir, ortalar statik
    dilime duser. Clef'e ulasilamazsa mevcut statik dilim davranisina doner."""
    mod = {"full":"eksiksiz", "smart":"akilli"}.get(mod, mod)
    if mod not in ("eksiksiz", "akilli"):
        raise ValueError(T("Mode must be full or smart", "Mod eksiksiz veya akilli olmalı"))
    sabit_adaylari = {}
    kayitlar = []
    for m in mesajlar:
        k = kayit_yap(m, sabit_adaylari, eksiksiz=mod == "eksiksiz")
        if k is None:
            continue
        if mod == "akilli":
            for a in ("c", "e", "e2"):
                if a in k:
                    k[a] = bosluk_temizle(k[a])
            # Arac cagrisi argumanlari da metin: govdeye uygulanan kod-farkindali
            # temizlik onlara da uygulanir. Olcum: argumanlar toplam karakterin
            # %35,7'si — temizlikten muaf tutulmalari icin sebep yok.
            for g in k.get("tc") or []:
                if isinstance(g.get("a"), str):
                    g["a"] = bosluk_temizle(g["a"])
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
            k["c"] = f"[CLEF-SILINDI noul={noul} — kaynak girdide duruyor]"
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
        if c.startswith(("[CLEF-SILINDI", "[JEV-SILINDI")) or c.startswith("[AYNI-#"):
            continue
        h = hashlib.blake2b(c.encode("utf-8", "replace"), digest_size=16).digest()
        ilk = ilk_gorulen.get(h)
        if ilk is None:
            ilk_gorulen[h] = i
            continue
        k["c"] = f"[AYNI-#{ilk} — bu arac ciktisi {ilk} numarali iletle birebir ayni; gerekirse orayi oku]"
        tekrar_sayisi += 1
        tekrar_bayt += len(c) - len(k["c"])

    # ── ARAC CAGRISI ARGUMANI TEKRAR AYIKLAMA (2026-09-20 olcum: 200+ karakterlik
    # argumanlarin %22,8'i paket icinde BIREBIR tekrar — 10 pakette ~87k token).
    # Ayni gerekce, ayni mekanizma: ikinci kopya geri-basvuruya dusuyor. Ayri bir
    # gorulen-havuzu kullanilir; ciktiyla argumani ayni kovaya koymak "#N"i
    # belirsiz yapardi (biri iletin govdesi, oteki cagrinin argumani).
    arg_gorulen = {}
    for i, k in enumerate(kayitlar) if mod != "eksiksiz" else []:
        for g in k.get("tc") or []:
            a = g.get("a")
            if not isinstance(a, str) or len(a) < 200 or a.startswith("[AYNI-ARG-#"):
                continue
            h = hashlib.blake2b(a.encode("utf-8", "replace"), digest_size=16).digest()
            ilk = arg_gorulen.get(h)
            if ilk is None:
                arg_gorulen[h] = i
                continue
            g["a"] = (f"[AYNI-ARG-#{ilk} — bu arac argumani {ilk} numarali iletteki "
                      f"cagrinin argumani ile birebir ayni; gerekirse orayi oku]")
            tekrar_sayisi += 1
            tekrar_bayt += len(a) - len(g["a"])

    sozluk = Sozluk()
    # A literal codec marker in source text must never become a dictionary lookup.
    # Keep LZMA compression, but omit token substitution for such packages.
    fields = [value for k in kayitlar for value in (k.get("c"), k.get("e"), k.get("e2"))]
    fields.extend(g.get("a") for k in kayitlar for g in k.get("tc", []))
    if not any(isinstance(value, str) and JETON_RE.search(value) for value in fields):
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
    for k in (kayitlar[-son_n:] if son_n > 0 else []):
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
    return os.path.expanduser(session_env("HERMES_STATE_DB") or
                             os.path.join(session_env("HERMES_HOME", "~/.hermes"), "state.db"))


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
                    "effect_disposition", "reasoning_details")]
        alanlar = ", ".join(f'"{c}"' for c in secilen)
        satirlar = con.execute(
            f"SELECT {alanlar} FROM messages WHERE session_id=? ORDER BY id",
            (sid,)).fetchall()
        baslik = baslik_bul(con, sid)
    finally:
        con.close()
    return sid, [dict(zip(secilen, s)) for s in satirlar], baslik


def oturum_sec(con, session_id):
    if session_id in ("aktif", "active", "current", "son", "en-son", "last", "latest"):
        identity = session_identity()
        if identity:
            if identity[0] != "hermes":
                raise ValueError(T("Use this Codex thread's rollout file, not a Hermes last-session selector",
                                   "Bu Codex sohbetinin rollout dosyasını kullan; Hermes son-oturum seçicisini kullanma"))
            sid = session_env("HERMES_SESSION_ID")
            key = session_env("HERMES_SESSION_KEY")
            for candidate in (sid, key):
                if candidate and con.execute("SELECT 1 FROM messages WHERE session_id=?", (candidate,)).fetchone():
                    return candidate
            if key:
                try:
                    rows = con.execute("SELECT id FROM sessions WHERE session_key=?", (key,)).fetchall()
                    if len(rows) == 1:
                        return rows[0][0]
                except sqlite3.OperationalError:
                    pass
            raise ValueError(T("Caller session not found; give its exact ID", "Komutu veren oturum bulunamadı; tam kimliğini ver"))
        if session_id in ("aktif", "active", "current"):
            raise ValueError(T("Caller session context is missing", "Komutu veren oturum bağlamı yok"))
        raise ValueError(T("Caller context missing; give an exact session ID", "Oturum bağlamı yok; tam oturum kimliğini ver"))
    if session_id in ("en-uzun", "en-buyuk"):
        return con.execute("SELECT session_id FROM messages GROUP BY session_id "
                           "ORDER BY SUM(COALESCE(LENGTH(content),0)+COALESCE(LENGTH(reasoning),0)) DESC LIMIT 1"
                           ).fetchone()[0]
    exact = con.execute("SELECT DISTINCT session_id FROM messages WHERE session_id=?", (session_id,)).fetchall()
    if exact:
        return exact[0][0]
    escaped = str(session_id).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    es = con.execute("SELECT DISTINCT session_id FROM messages WHERE session_id LIKE ? ESCAPE '\\'",
                     (escaped + "%",)).fetchall()
    if len(es) != 1:
        # Yanlislikla '+' ve if/else onceligi karisiyordu; 0-eslesme durumunda
        # 'N eslesme:' metni None ile toplanip TypeError patlatabilirdi.
        raise ValueError(
            (f"'{session_id}' -> {len(es)} eslesme: "
             + ", ".join(s[0] for s in es[:6])) if es
            else f"'{session_id}': eslesme yok")
    return es[0][0]


def _dokum_kaynak(yol):
    """Claude Code oturum disa aktarimi (.zip veya transcript.jsonl) -> kaynak.

    Zip ise gecici dizine acilir; icindeki transcript.jsonl ve metadata.json
    kullanilir. Boylece `czip birlestir --dokum=...` Hermes disindaki oturumlari
    da ayni birlestirme kurallariyla ayni pakete alabilir.
    """
    import ccd_dokum as _c
    yol = os.path.expanduser(yol)
    if yol.lower().endswith(".zip"):
        import tempfile
        import zipfile
        d = tempfile.mkdtemp(prefix="czip-dokum-")
        with zipfile.ZipFile(yol) as z:
            z.extractall(d)
        ic = os.path.join(d, "transcript.jsonl")
        if not os.path.exists(ic):
            raise ValueError("zip icinde transcript.jsonl yok: %s" % yol)
        meta = os.path.join(d, "metadata.json")
        baslik = sid = None
        if os.path.exists(meta):
            try:
                with open(meta, encoding="utf-8") as f:
                    mj = json.load(f)
                baslik, sid = mj.get("title"), mj.get("sessionId")
            except Exception:
                pass
        return _c.kaynak(ic, sid=sid, baslik=baslik)
    return _c.kaynak(yol)


def yeni_ad(baslik, sid=""):
    """Paketten sonra acilacak oturumun adi: 'Czip-' + eski ad.

    Neden onek: yeni oturum eski adla acilinca oturum listesinde iki ayni isim
    yan yana durur; hangisi devam, hangisi paketlenmis kaynak ancak tarihe
    bakarak anlasilir. Onek bunu tek bakista ayirir. Ustuste paketlemede
    'Czip-Czip-...' birikmesin diye onek yalniz bir kez eklenir.
    """
    ad = str(baslik or sid or "oturum").strip()
    # Birlesik paket basligindaki "(+2 oturum birlesik)" kuyrugu oturum adina
    # girmez: 60 karakterlik sinirda asil adi kirpar ve zaten paket icinde yazili.
    ad = re.sub(r"\s*\(\+\d+ oturum birlesik\)\s*$", "", ad).strip() or (sid or "oturum")
    if ad.lower().startswith("czip-"):
        return ad[:60]
    return ("Czip-" + ad)[:60]


def son_paket(dizin=None):
    """Latest registered package owned by this caller/profile; no global fallback."""
    owner = owner_key()
    if owner:
        matches = [value for value in _kayit_oku().values() if isinstance(value, dict) and
                   value.get("owner") == owner and os.path.isfile(value.get("yol", ""))]
        return max(matches, key=lambda v: v.get("created_ns", 0))["yol"] if matches else None
    return None


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
            # A real user request may start with a Markdown link: [label](url).
            # The role already excludes tool/system messages, so keep it.
            if c and not c.startswith("──"):
                istekler.append((i, c[:96]))
    tekrar = sum(1 for k in kayitlar
                 if isinstance(k.get("c"), str) and k["c"].startswith("[AYNI-#"))
    son_n = max(0, min(int(son_n), 6))
    istek_max = max(0, min(int(istek_max), 40))
    tail = []
    for i in range(max(0, len(kayitlar)-son_n), len(kayitlar)):
        item = tam_ilet(kayitlar[i], sozluk, reasoning=False)
        content = str(item.get("icerik") or "")
        tail.append({"i":i, "rol":item["rol"], "icerik":content[:900],
                     "kirpildi":len(content)>900,
                     "arac":[str(call.get("name", ""))[:80] for call in item.get("tool_calls", [])[:8]]})
    return {
        "baslik": meta.get("baslik") or meta.get("b") or os.path.basename(dosya),
        "toplam": len(kayitlar),
        "roller": roller,
        "araclar": sorted(araclar.items(), key=lambda x: -x[1])[:12],
        "tekrar_isaretli": tekrar,
        "istekler": istekler[-istek_max:] if istek_max else [],
        "istek_toplam": len(istekler),
        "son": tail,
        "talimat": T("INDEX IS NOT LOADED. This is a map, not the transcript. "
                    "To get content: `czip ara <id> \"<query>\"` for keyword hits, "
                    "then `czip aralik <id> <a-b>` for the exact messages. "
                    "Never dump the whole package. Work from the last messages below.",
                    "Bu harita döküm değildir. İçerik için czip ara <kimlik> \"sorgu\", ardından czip aralik <kimlik> <ilk-son> kullan. Paketin tamamını yükleme."),
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
    "task": "gorev",
    "prompt": "gorev",
    "yardım": "yardim",
    "paket": "paketle",
    "birleştir": "birlestir",
    "aralık": "aralik",
}

def _karar_kaynagi(ad):
    """Karar kaynagi etiketini ekrana basarken cevirir.

    Veri degeri ('yerel'/'jev') DEGISMEZ — birlestir.py karsilastirmalari ona
    bakiyor; cevrilen yalniz gosterim.
    """
    return T("local", "yerel") if ad == "yerel" else str(ad)


# ------------------------------------------------------------- CLI
def _main(argv):
    from czip_runtime import output_language
    values = list(argv); chosen = None
    for flag in ("--lang", "--dil"):
        if flag in values:
            i = values.index(flag)
            if i+1 >= len(values):
                print(hata(T("Language value required", "Dil değeri gerekli"))); return 2
            chosen = values[i+1]; del values[i:i+2]
    for value in list(values):
        if value.startswith(("--lang=", "--dil=")):
            chosen = value.split("=",1)[1]; values.remove(value)
    if chosen and chosen not in ("tr", "en"):
        print(hata(T("Language must be tr or en", "Dil tr veya en olmalı"))); return 2
    flags = {"--full":"--eksiksiz", "--smart":"--akilli", "--clef":"--jev", "--auto":"--oto", "--auto-archive":"--oto-pasif", "--no-archive":"--pasif-yok"}
    prefixes = {"--days=":"--gun=", "--export=":"--dokum=", "--type=":"--tip=", "--title=":"--baslik=", "--query=":"--ara="}
    normalized = []
    for value in values:
        value = flags.get(value, value)
        for old, new in prefixes.items():
            if value.startswith(old): value = new + value[len(old):]; break
        normalized.append(value)
    with output_language(chosen):
        try:
            return _main_ici(normalized)
        except (ValueError, OSError) as error:
            print(hata(error)); return 2

def _main_ici(argv):
    """Parse the shared bilingual command surface."""
    if not argv or argv[0] in ("-h", "--help", "yardim", "help", "yardım"):
        from czip_commands import help_text
        print(help_text())
        return 0
    emir, kalan = argv[0], argv[1:]
    if emir in ("prompt", "promt", "plan"):
        kind = "plan" if emir == "plan" else "promt"
        kalan = [*(kalan or ["list"]), "--tip=" + kind]; emir = "task"
    if emir in ("threshold", "esik", "eşik"):
        kalan = ["esik", *kalan]; emir = "ayar"
    if emir in ("end", "sonlandir", "sonlandır"):
        from plugin import _son
        print(_son(" ".join(__import__("shlex").quote(x) for x in kalan)))
        return 0
    # English is the primary CLI; the original Turkish verbs keep working.
    emir = _ALIAS.get(emir, emir)
    # /czip_50 , /czip_%75 , czip 50%  -> shorthand for "ayar esik <n>"
    _m = re.fullmatch(r"%?(\d{1,3})%?", emir)
    if _m:
        kalan = ["esik", _m.group(1)] + list(kalan)
        emir = "ayar"
    elif emir in ("auto", "oto"):
        kalan = [] if not kalan or kalan[0] in ("status", "durum") else ["oto", {"aç":"on", "ac":"on", "kapat":"off"}.get(kalan[0], kalan[0])]
        emir = "ayar"

    if emir == "verify-source":
        if len(kalan) != 3: raise ValueError("verify-source PACKAGE_ID PATH SOURCE_ID")
        print(json.dumps(verify_package_source(*kalan), ensure_ascii=False)); return 0
    if emir == "gorev":
        # Onaylanmis promt/plan deposu (bkz. gorev.py). Govde stdin'den okunur:
        # metni argumana koymak uzun promptlarda kotalama/kacis derdi yaratirdi.
        import gorev as _g
        alt = (kalan[0] if kalan and not kalan[0].startswith("--") else "listele")
        bayrak = {}
        for a in kalan:
            if a.startswith("--") and "=" in a:
                k, v = a[2:].split("=", 1)
                bayrak[k] = v
        if alt in ("kaydet", "save", "ekle"):
            govde = sys.stdin.read() if not sys.stdin.isatty() else ""
            try:
                r = _g.kaydet(bayrak.get("tip", "promt"), bayrak.get("baslik"),
                              govde, ham=bayrak.get("ham"),
                              etiketler=[e for e in (bayrak.get("etiket") or "").split(",") if e])
            except Exception as e:
                print(hata(e))
                return 1
            print(T("SAVED", "KAYDEDILDI") + f"  {r['kid']}  [{r['tip']}]  {r['baslik']}")
            print(f"  {r['yol']}  ({r['paket_bayt']} bayt, {r['karakter']} karakter)")
            print(T("  read again: ", "  yeniden oku: ") + f"czip gorev oku {r['kid']}")
            return 0
        if alt in ("oku", "read", "show"):
            hedef = next((a for a in kalan[1:] if not a.startswith("--")), "son")
            try:
                r = _g.oku(hedef, tip=bayrak.get("tip"))
            except Exception as e:
                print(hata(e))
                return 1
            print(f"[{r['tip'].upper()}] {r['baslik']}")
            if r["ham"]:
                print(T("--- raw request ---", "--- ham istek ---"))
                print(r["ham"])
            print(T("--- approved ---", "--- onaylanmis ---"))
            print(r["metin"])
            return 0
        if alt in ("listele", "list", "ls"):
            try:
                n = int(bayrak.get("n", "20"))
            except ValueError:
                n = 20
            kayitlar = _g.listele(tip=bayrak.get("tip"), n=n, sorgu=bayrak.get("ara"))
            if not kayitlar:
                print(T("no saved task yet", "kayitli gorev yok"))
                return 0
            for k in kayitlar:
                print(f"{k.get('kid') or '------'} | {k.get('t','')} | "
                      f"{(k.get('tip') or '?'):5} | {(k.get('baslik') or '')[:60]}")
            return 0
        print(hata(T("unknown subcommand: %s (save|read|list)",
                     "bilinmeyen alt komut: %s (kaydet|oku|listele)") % alt))
        return 1

    if emir == "birlestir":
        if any(x in ("oto", "auto") for x in kalan):
            raise ValueError(T("Merge requires explicit session IDs", "Birleştirme açık oturum kimlikleri gerektirir"))
        # Ayni isi yapan 2+ oturumu TEK pakette birlestirir (bkz. birlestir.py).
        # Argumansiz: salt-okunur aday taramasi, hicbir sey yazmaz.
        import birlestir as _b
        if any(x.startswith("--") and x not in ("--jev", "--eksiksiz", "--akilli", "--pasif-yok") and not x.startswith(("--gun=", "--dokum=")) for x in kalan):
            raise ValueError(T("Unknown merge option", "Bilinmeyen birleştirme seçeneği"))
        jev = "--jev" in kalan
        mod = "akilli" if "--akilli" in kalan else "eksiksiz"
        gun = 7
        for a in kalan:
            if a.startswith("--gun="):
                try:
                    gun = max(1, int(a.split("=", 1)[1]))
                except ValueError:
                    pass
        idler = [a for a in kalan if not a.startswith("--") and a != "oto"]
        dokumler = [a.split("=", 1)[1] for a in kalan if a.startswith("--dokum=")]
        if dokumler:
            # Hermes disi dokumler (Claude Code disa aktarimi): state.db'de yok,
            # ama ayni birlestirme kurallarini hak ediyor. state.db id'leriyle
            # karistirilabilir — ikisi de kaynak listesine girer.
            kaynaklar = []
            for y in dokumler:
                try:
                    kaynaklar.append(_dokum_kaynak(y))
                except Exception as _e:
                    print(hata(T("cannot read transcript %s: %s",
                                 "dokum okunamadi %s: %s") % (y, _e)))
                    return 1
            for i in idler:
                sid, msj, bas = oturum_oku(i)
                kaynaklar.append({"sid": sid, "mesaj": msj, "baslik": bas or sid})
            if len(kaynaklar) < 2:
                print(hata(T("merging needs at least 2 sources",
                             "birlestirme icin en az 2 kaynak gerekir")))
                return 1
            mesajlar, baslik, rapor = _b.kaynaklari_birlestir(kaynaklar)
            d = os.path.expanduser(PAKET_DIZIN)
            os.makedirs(d, exist_ok=True)
            temiz = re.sub(r"[^A-Za-z0-9-]+", "-", baslik[:48]).strip("-") or "birlesik"
            yol = unique_package_path(d, "merged-" + baslik)
            r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev)
            kid = id_ata(r["yol"], baslik)
            print(T("MERGED: ", "BIRLESTIRILDI: ") + f"{r['yol']}\n  ID={kid}")
            for x in rapor["ayrinti"]:
                print(f"    - {x['sid']}  {x['ilet']} " + T("msgs  ", "ilet  ")
                      + f"{(x['baslik'] or '')[:45]}")
            print(f"  {rapor['kaynak_ilet']} -> {rapor['birlesik_ilet']} "
                  + T("msgs", "ilet")
                  + T("  (duplicates dropped: ", "  (yinelenen atilan: ")
                  + f"{rapor['yinelenen_atilan']})")
            print(f"  {r['kaynak_bayt']}->{r['paket_bayt']}B  "
                  + T(f"ratio={r['oran']}x", f"oran={r['oran']}x"))
            print(T("  new session name: ", "  YENI OTURUM ADI: ") + yeni_ad(baslik))
            print(T("  read: czip read ", "  oku: czip oku ") + kid)
            print(T("  NOTE: transcript sources untouched (read-only).",
                    "  NOT: dokum kaynaklarina dokunulmadi (salt-okunur)."))
            return 0
        if len(idler) < 2:
            _, ciftler = _b.adaylari_bul(gun=gun)
            if not ciftler:
                print(T("No similar session to merge in the last %d days." % gun,
                        "Son %d gunde birlestirilecek benzer oturum yok." % gun))
                return 0
            gruplar, iz = _b.gruplari_kur(ciftler, jev=jev)
            jb = iz.get("jev") or {}
            print(T(f"CANDIDATE SCAN — last {gun} days, {len(ciftler)} similar pairs",
                    f"ADAY TARAMASI — son {gun} gun, {len(ciftler)} benzer cift")
                  + (f"  | Clef: {jb.get('hata') or str(jb.get('cevap')) + T(' answers', ' cevap')}" if jev else ""))
            for k in iz["kararlar"][:12]:
                print(f"  {T('ACCEPT', 'KABUL') if k['kabul'] else T('  rej', '  red')}  {k['a']} <-> {k['b']}"
                      f"  {_karar_kaynagi(k['kaynak'])}={k['skor']:.2f}")
                print(f"        A: {k['a_ozet']}")
                print(f"        B: {k['b_ozet']}")
            if not gruplar:
                print(T("\nNo group passed the threshold.", "\nEsigi gecen grup yok."))
                return 0
            print(T("\nGROUPS:", "\nGRUPLAR:"))
            for i, g in enumerate(gruplar, 1):
                print(f"  {i}) " + "  ".join(g))
            print(T("\nTo apply: czip merge ", "\nUygulamak için: czip birlestir ") + " ".join(gruplar[0]))
            return 0
        mesajlar, baslik, rapor = _b.oturumlari_birlestir(idler)
        d = os.path.expanduser(PAKET_DIZIN)
        os.makedirs(d, exist_ok=True)
        temiz = re.sub(r"[^A-Za-z0-9-]+", "-", baslik[:48]).strip("-") or "birlesik"
        yol = unique_package_path(d, "merged-" + baslik)
        r = sikistir(mesajlar, yol, mod, baslik=baslik, jev=jev)
        kid = id_ata(r["yol"], baslik)
        print(T("MERGED: ", "BIRLESTIRILDI: ") + f"{r['yol']}\n  ID={kid}")
        for x in rapor["ayrinti"]:
            print(f"    - {x['sid']}  {x['ilet']} " + T("msgs", "ilet"))
        print(f"  {rapor['kaynak_ilet']} -> {rapor['birlesik_ilet']} " + T("msgs", "ilet")
              + T("  (duplicates dropped: ", "  (yinelenen atilan: ")
              + f"{rapor['yinelenen_atilan']})")
        print(f"  {r['kaynak_bayt']}->{r['paket_bayt']}B  "
                  + T(f"ratio={r['oran']}x", f"oran={r['oran']}x"))
        print(T("  read: czip read ", "  oku: czip oku ") + kid)
        print(T("  Source sessions remain unchanged. Use end with an exact ID to archive one.",
                "  Kaynak oturumlar aynı kalır. Bir oturumu arşivlemek için tam kimlikle end kullan."))
        print(T("\n  NEW SESSION NAME: ", "\n  YENI OTURUM ADI: ") + yeni_ad(baslik))
        print(T("\n  HANDOVER (first thing in the new session):",
                "\n  DEVIR TALIMATI (yeni oturumda ilk is):"))
        print(T(f"    czip read {kid}  -> find the open work, continue where it stopped.",
                f"    czip oku {kid}  -> kalan acik isi tespit et, kaldigi yerden devam et."))
        return 0
    if emir == "gerial":
        import birlestir as _b
        if not kalan:
            d = _b.gerial_dizin()
            if not os.path.isdir(d) or not os.listdir(d):
                print(T("Nothing to undo.", "Geri alinacak kayit yok."))
                return 0
            print(T("Undo records:", "Geri alma kayitlari:"))
            for f in sorted(os.listdir(d), reverse=True)[:10]:
                print("  " + f)
            print(T("\nUsage: czip undo <file>", "\nKullanim: czip gerial <dosya>"))
            return 0
        g = _b.geri_al(kalan[0])
        print(T("UNDONE: %d sessions active again",
                "GERI ALINDI: %d oturum yeniden aktif") % len(g["geri_alinan"]))
        for x in g["geri_alinan"]:
            print("  " + x)
        return 0
    if emir == "paketle":
        flags = [x for x in kalan if x.startswith("--")]
        targets = [x for x in kalan if not x.startswith("--")]
        if any(x in flags for x in ("--oto", "--oto-pasif")):
            raise ValueError(T("Use merge with explicit session IDs; packing never auto-merges", "Açık oturum kimlikleriyle birlestir kullan; paketleme otomatik birleştirmez"))
        if set(flags) - {"--eksiksiz", "--akilli", "--jev", "--json", "--yalniz"}:
            raise ValueError(T("Unknown packing option", "Bilinmeyen paketleme seçeneği"))
        if len(targets) > 1 or (not targets and not session_identity()):
            raise ValueError(T("Give one exact session ID or transcript path", "Tek bir tam oturum kimliği veya dosya yolu ver"))
        target = targets[0] if targets else "active"
        if os.path.isfile(os.path.expanduser(target)):
            messages = girdi_ayristir(target)
            title = os.path.splitext(os.path.basename(target))[0]
            source = "file:" + os.path.abspath(os.path.expanduser(target))
            try:
                with open(os.path.expanduser(target), encoding="utf-8") as stream:
                    data = json.load(stream)
                if isinstance(data, dict): title = data.get("title") or data.get("baslik") or title
            except (ValueError, OSError):
                pass
        else:
            source, messages, title = oturum_oku(target)
        if not messages:
            raise ValueError(T("The selected source is empty", "Seçilen kaynak boş"))
        mode = "akilli" if "--akilli" in flags else "eksiksiz"
        path = unique_package_path(PAKET_DIZIN, title or source)
        result = sikistir(messages, path, mode, baslik=title, jev="--jev" in flags)
        kid = id_ata(result["yol"], title, source_id=source)
        payload = {"ok": True, "kid": kid, "yol": result["yol"], "source_id": source,
                   "ilet": result["mesaj"], "oran": result["oran"], "kaynak_bayt": result["kaynak_bayt"],
                   "paket_bayt": result["paket_bayt"], "mod": result["mod"], "parca": result["parca"],
                   "baslik": title or source, "yeni_oturum_adi": yeni_ad(title, source)}
        if "--json" in flags:
            print(json.dumps(payload, ensure_ascii=False)); return 0
        print(T("PACKED: ", "PAKETLENDİ: ") + result["yol"])
        print("  ID=" + kid)
        print(T("  %d messages | %d -> %d bytes", "  %d ileti | %d -> %d bayt") %
              (result["mesaj"], result["kaynak_bayt"], result["paket_bayt"]))
        print(T("  Read: czip map ", "  Oku: czip harita ") + kid)
        print(T("  Only this source was packed; source sessions remain open.", "  Yalnız bu kaynak paketlendi; kaynak oturumlar açık kalır."))
        return 0
    if emir == "listele":
        kayit = _kayit_oku()
        if not kayit:
            print(T("registry empty — no package has an id yet",
                    "kayit bos — henuz id'li paket yok"))
            return 0
        for kid, v in sorted(kayit.items(), key=lambda kv: (kv[1].get("t") or ""), reverse=True):
            print(f"{kid} | {v.get('t','')} | {(v.get('baslik') or '')[:60]}")
        return 0
    if emir == "ayar":
        # czip ayar                 -> show settings
        # czip ayar esik 50         -> offer at %50 of the context window
        # czip ayar kademe 50,75,90 -> escalating tiers
        # czip ayar kalan 70k      -> pack when this much room is left
        # czip ayar mutlak 64000   -> pack when prompt tokens hit this count
        # czip ayar oto on|off      -> decide automatically instead of asking
        # czip ayar oto-pasif on|off
        import ayar as _a
        if not kalan:
            a = _a.oku()
            print(T("czip settings (%s)", "czip ayarlari (%s)") % _a.AYAR_YOLU)
            print("  esik_oran : %%%d" % round(a["esik_oran"] * 100)
                  + T("   (cap)", "   (tavan)"))
            kt = int(a.get("kalan_token") or 0)
            print("  kalan_token: " + (f"{kt:,}" if kt else "off")
                  + T("   (pack when this much room is left)",
                      "   (bu kadar yer kalinca paketle)"))
            mt = int(a.get("mutlak_token") or 0)
            print("  mutlak_token: " + (f"{mt:,}" if mt else "off")
                  + T("   (pack when prompt tokens hit this count)",
                      "   (prompt token bu sayiya gelince paketle)"))
            for ctx in (262144, 1000000):
                e = _a.esik_token(ctx, a)
                # Uc ayar ayni anda acikken hangisinin tetigi belirledigi
                # gorunmuyordu; kazanani yaz ki olu ayar fark edilsin.
                adaylar = [(float(a.get("esik_oran", 0.5)) * ctx, "esik_oran")]
                if kt:
                    adaylar.append((ctx - kt, "kalan_token"))
                if mt:
                    adaylar.append((mt, "mutlak_token"))
                kazanan = min(adaylar)[1]
                print(T(f"     {ctx//1000}k window -> fires at {e:,} (%{100*e/ctx:.0f})"
                        f"  rule: {kazanan}",
                        f"     {ctx//1000}k pencere -> {e:,} token (%{100*e/ctx:.0f})"
                        f"  kural: {kazanan}"))
            print("  kademeler : " + ", ".join("%%%d" % round(k * 100) for k in a["kademeler"]))
            print("  oto       : " + ("ON" if a["oto"] else "off")
                  + T("   (on = decide and pack without asking)",
                      "   (on = sormadan karar verip paketler)"))
            print("  oto_pasif : " + ("ON" if a["oto_pasif"] else "off"))
            print(T("  decision gate: ", "  karar kapısı: ") + ("Clef" if a.get("clef", a.get("jev", False)) else T("off", "kapalı")))
            print(T("  gate is local; cloud fallback is disabled", "  karar kapısı yerel; bulut yedeği kapalı"))
            return 0
        anahtar = kalan[0].lower()
        deger = kalan[1] if len(kalan) > 1 else ""
        acik = deger.lower() in ("on", "aç", "ac", "acik", "açık", "1", "true", "evet", "yes")
        if anahtar in ("oto", "clef", "gate", "kapi", "jev") and deger.lower() not in ("on", "aç", "ac", "acik", "açık", "1", "true", "evet", "yes", "off", "kapat", "kapali", "kapalı", "0", "false", "hayir", "hayır", "no"):
            raise ValueError(T("Use on or off", "aç veya kapat kullan"))
        try:
            if anahtar in ("esik", "threshold"):
                o = float(deger.rstrip("%")) / (100.0 if float(deger.rstrip("%")) > 1 else 1.0)
                if not 0 < o <= 1: raise ValueError("threshold")
                _a.yaz(esik_oran=o)
                print(T("threshold -> %%%d", "esik -> %%%d") % round(o * 100))
            elif anahtar in ("kademe", "tiers"):
                ks = sorted({float(x.strip().rstrip("%")) / 100.0 if float(x.strip().rstrip("%")) > 1
                             else float(x.strip()) for x in deger.split(",") if x.strip()})
                if not ks or any(not 0 < x <= 1 for x in ks): raise ValueError("tiers")
                _a.yaz(kademeler=ks)
                print(T("tiers -> %s", "kademeler -> %s")
                      % ", ".join("%%%d" % round(k * 100) for k in ks))
            elif anahtar in ("kalan", "kalan_token", "headroom"):
                d = deger.strip().lower().replace(".", "").replace(",", "")
                n = int(float(d[:-1]) * 1000) if d.endswith("k") else int(float(d))
                _a.yaz(kalan_token=max(0, n))
                print(T("headroom -> %s tokens", "kalan_token -> %s token")
                      % (f"{max(0, n):,}" if n else "off"))
            elif anahtar in ("mutlak", "mutlak_token", "token"):
                d = deger.strip().lower().replace(".", "").replace(",", "").replace("_", "")
                n = int(float(d[:-1]) * 1000) if d.endswith("k") else int(float(d))
                _a.yaz(mutlak_token=max(0, n))
                print(T("absolute -> %s tokens", "mutlak_token -> %s token")
                      % (f"{max(0, n):,}" if n else "off"))
            elif anahtar == "oto":
                _a.yaz(oto=acik)
                print("oto -> " + ("ON" if acik else "off"))
            elif anahtar in ("oto-pasif", "oto_pasif"):
                raise ValueError(T("Automatic closure is disabled; use end with an exact ID", "Otomatik kapatma devre dışı; tam kimlikle end kullan"))
            elif anahtar in ("clef", "gate", "kapi", "jev"):
                _a.yaz(clef=acik)
                print("clef -> " + ("ON" if acik else "off"))
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
        tam = "--eksiksiz" in kalan or "--tam" in kalan
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
        # Tirnaksiz cok kelimeli sorgu ilk kelimeye dusmesin (bkz. asagidaki
        # tarama yolunda ayni not): sessizce daralan arama yanlis "yok" uretir.
        _sorgu = " ".join(x for x in kalan if not x.startswith("--"))
        # --paket/--satir eskiden yalniz tarama yolunda ayristiriliyordu;
        # indeksli hizli yolda sabit limit kullanilip bayraklar sessizce
        # yok sayiliyordu (denetim 21.09). Simdi burada okunur.
        def _bayrak(onek, varsayilan):
            for a in kalan:
                if a.startswith(onek + "="):
                    try:
                        return max(1, int(a.split("=", 1)[1]))
                    except ValueError:
                        pass
            return varsayilan
        _pmax = _bayrak("--paket", 25)
        _smax = _bayrak("--satir", 3)
        # Fast path: use the index when it exists; fall back to scanning packages.
        try:
            import depo as _d
            if os.path.exists(_d.yol()) and kalan and "--tara" not in kalan:
                r = _d.ara(_sorgu, limit=_pmax, paket_limit=_smax)
                print(T("STORE SEARCH %r — %d hits", "DEPO ARAMA %r — %d isabet")
                      % (_sorgu, r["total"]))
                if r.get("not"):
                    print("  (%s)" % r["not"])
                for pk in r["packages"]:
                    ad = pk["kid"] or os.path.basename(pk["path"])
                    # STAIR: ajan yalniz gecerli i araligini gorsun (uydurma id yok)
                    ar = pk.get("aralik") or "?"
                    print("\n%s  [%s]  %s  aralik=%s" % (
                        ad, time.strftime("%d.%m %H:%M", time.localtime(pk["mtime"])),
                        str(pk["title"])[:60], ar))
                    for h in pk["hits"]:
                        print("   #%s %s: %s" % (h["i"], h["role"], h["snippet"][:150]))
                    # STAIR 2. asama: blok onerileri — konu/blok katmaninda bulunan
                    # araliklar. i alani 'bas-son' formatindadir; ayni gecerlilik
                    # sozlesmesi (0..msgs-1) depo.ara'da dogrulanmis gelir.
                    for b in pk.get("blok", []):
                        print("   BLOK aralik=%s: %s" % (b["aralik"], b["snippet"][:150]))
                    if pk["kid"]:
                        print("   -> czip range %s <i-i>   # i must be in %s" % (pk["kid"], ar))
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
            print(T("ERROR: usage -> czip asearch \"<query>\" [--paket=N] [--satir=M]",
                    "HATA: kullanim -> czip arsivara \"<sorgu>\" [--paket=N] [--satir=M]"))
            return 2
        # Tirnaksiz yazilan cok kelimeli sorgu sessizce ilk kelimeye dusmesin:
        # 'czip asearch czip gorev deposu' eskiden yalniz 'czip' ariyordu ve
        # kullaniciya bunu soylemiyordu — sessizce daralan arama, yanlis "yok"
        # cevabi uretir. Bayrak olmayan tum argumanlar sorguya katilir.
        sorgu = " ".join(a for a in kalan if not a.startswith("--"))
        pmax = 40
        smax = 2
        for a in kalan[1:]:
            if a.startswith("--paket="):
                pmax = max(1, int(a.split("=", 1)[1]))
            elif a.startswith("--satir="):
                smax = max(1, int(a.split("=", 1)[1]))
        d = os.path.expanduser(PAKET_DIZIN)
        if not os.path.isdir(d):
            print(T("No package directory:", "Paket dizini yok:"), d)
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
        print(T("\nTOTAL: %d hits / %d packages",
                "\nTOPLAM: %d isabet / %d paket") % (toplam, bulunan_paket))
        if not toplam:
            print(T("(found in no package)", "(hicbir pakette bulunamadi)"))
        return 0
    if emir == "ara":
        if len(kalan) < 2:
            print(T("ERROR: usage -> czip search <id|son> \"query\"",
                    "HATA: kullanim -> czip ara <id|son> \"sorgu\""))
            return 2
        yol = id_coz(kalan[0])
        if not yol or not os.path.exists(yol):
            print(T("ERROR: package not found:", "HATA: paket bulunamadi:"), kalan[0])
            return 2
        try:
            sonuclar = paket_ara(yol, " ".join(kalan[1:]))
        except ValueError as e:
            print(T("ERROR:", "HATA:"), e)
            return 2
        print(json.dumps({"paket": os.path.basename(yol), "bulunan": len(sonuclar),
                          "sonuclar": sonuclar}, ensure_ascii=False))
        print(T("HINT: full text for an i -> czip range %s \"i\" (or i-i2)",
                "IPUCU: ilgili i icin tam metin -> czip aralik %s \"i\" (veya i-i2)") % kalan[0])
        return 0
    if emir == "oku":
        if len(kalan) > 2:
            raise ValueError(T("Read takes a package and optional range", "Oku bir paket ve isteğe bağlı aralık alır"))
        return _main_ici(["aralik" if len(kalan) == 2 else "harita", *kalan])
    if emir == "harita":
        # RAG-oncelikli okuma: tam indeks YERINE ince harita (~1.5k token).
        yol = id_coz(kalan[0] if kalan else None)
        if not yol or not os.path.exists(yol):
            print(T("ERROR: package not found:", "HATA: paket bulunamadi:"),
                  kalan[0] if kalan else "(son)")
            return 2
        h = harita(yol)
        print(T("MAP %s | %d msgs | roles=%s | dedup=%d", "HARİTA %s | %d ileti | roller=%s | tekrar=%d")
              % (h["baslik"], h["toplam"],
                 ",".join("%s:%d" % kv for kv in sorted(h["roller"].items())),
                 h["tekrar_isaretli"]))
        if h["araclar"]:
            print(T("TOOLS ", "ARAÇLAR ") + " ".join("%s:%d" % kv for kv in h["araclar"]))
        print(T("REQUESTS %d total, last %d:", "İSTEKLER toplam %d, son %d:") % (h["istek_toplam"], len(h["istekler"])))
        for i, c in h["istekler"]:
            print("  %d| %s" % (i, c))
        # TAIL kompakt gosterim: JSON sarmalayicisi (alan adlari, tirnak, kacis)
        # olculdu -> son iletlerin %47'si sirf tören. Rol tek harfe iner,
        # icerik duz metin kalir. Okuyan AI icin kayip yok.
        print(T("TAIL (%d previews; exact text via range):", "SON İLETİLER (%d önizleme; tam metin için aralik):") % len(h["son"]))
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
        print(T("HOW ", "KULLANIM ") + h["talimat"])
        return 0
    if emir == "aralik":
        if len(kalan) < 2:
            print("HATA: kullanim -> czip aralik <id|son> <bas-bit>")
            return 2
        yol = id_coz(kalan[0])
        if not yol or not os.path.exists(yol):
            print(T("ERROR: package not found:", "HATA: paket bulunamadi:"), kalan[0])
            return 2
        try:
            iletler = mesaj_araligi(yol, kalan[1])
        except (ValueError, IndexError) as e:
            print("HATA:", e)
            return 2
        for it in iletler:
            print(json.dumps(it, ensure_ascii=False))
        return 0
    print(T(f"Unknown command: {emir}  (help: czip --help)",
            f"Bilinmeyen emir: {emir}  (yardim: czip --help)"))
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except ValueError as _e:
        # Kullanici girdisi hatalari (olmayan oturum vb.) traceback degil
        # tek satir hata olmali — czip-tasi plugin'i stderr'i parse ediyor.
        print("ERROR: %s" % _e, file=sys.stderr)
        sys.exit(2)
