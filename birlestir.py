# -*- coding: utf-8 -*-
"""birlestir.py — ayni isi yapan 2+ oturumu TEK .hkp paketinde birlestirir.

czip'in kardesi: czip tek oturumu sikistirir, bu modul once "bunlar gercekten
ayni is mi?" sorusunu cevaplar, sonra birlestirip tek pakete sikistirir.

Neden gerekli: Hermes'te ayni konu icin farkli zamanlarda birden cok oturum
aciliyor (telegram'dan bir tane, TUI'den bir tane, gece cron'undan bir tane).
Her biri ayri ayri buyuyor, hicbiri otekinin ne yaptigini bilmiyor, ve
state.db'de birikip sikistirma dongusunu tikiyorlar.

Karar asamalari (ucu de Jev'e sorulabilir, --jev ile):
  1) ADAY BULMA      — baslik + kullanici mesaji ortakligi (yerel, ucuz)
  2) AYNI IS MI?     — Jev noul skoru; esik alti aday elenir
  3) ARAC CIKTISI    — hkp.sikistir'in mevcut Jev budamasi (degismedi)

Jev'e ulasilamazsa 2. asama yerel benzerlik skoruna duser (fail-open degil:
esik yerel skorda daha yuksek tutulur, yani supheli olani birlestirmez).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

import hkp

# Ayni-is esikleri
JEV_BIRLESTIR = 0.60     # Jev noul bunun ustundeyse "ayni is" sayilir
YEREL_BIRLESTIR = 0.45   # Jev yoksa yerel Jaccard esigi (bilerek daha sikici)
ADAY_TABAN = 0.18        # bunun altindaki cift Jev'e bile sorulmaz
MIN_ILET = 2             # bu kadar iletten az oturum aday olmaz

_DURDURMA = {
    "bir", "bu", "ve", "ile", "icin", "için", "da", "de", "mi", "mu", "ne",
    "olarak", "var", "yok", "the", "and", "for", "you", "that", "this", "with",
}

# Zamanlanmis gorev promptlari kelimesi kelimesine ayni kaliptir: benzerlik
# hesabindan cikarilmazsa BIRBIRIYLE ALAKASIZ iki cron kosusu %99 benzer cikar.
_KALIP = [
    re.compile(r"\[IMPORTANT:\s*You are running as a scheduled cron job.*?\]",
               re.S | re.I),
    re.compile(r"\[IMPORTANT:\s*Background process .*?\]", re.S | re.I),
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I),
]


def _kalip_at(metin):
    """Her oturumda aynen tekrarlayan sistem kaliplarini metinden siler."""
    for k in _KALIP:
        metin = k.sub(" ", metin or "")
    return metin


# --------------------------------------------------------------- yardimcilar
def _kelimeler(metin):
    """Metni karsilastirmaya uygun anlamli kelime kumesine indirger."""
    ham = re.findall(r"[0-9a-zçğıöşüA-ZÇĞİÖŞÜ]{3,}", _kalip_at(metin).lower())
    return {k for k in ham if k not in _DURDURMA}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _oturum_ozeti(con, sid):
    """Oturumun kimligi: baslik + ilk/son kullanici mesajlari + iletsayisi."""
    baslik = hkp.baslik_bul(con, sid) or ""
    satirlar = con.execute(
        "SELECT role, content, COALESCE(timestamp,0) FROM messages "
        "WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    kullanici = [str(c) for r, c, _ in satirlar if r == "user" and c]
    zamanlar = [t for _, _, t in satirlar if t]
    return {
        "sid": sid,
        "baslik": baslik,
        "ilet": len(satirlar),
        "kullanici": kullanici,
        "bas": min(zamanlar) if zamanlar else 0,
        "son": max(zamanlar) if zamanlar else 0,
        # niyet = baslik + ilk 3 kullanici mesaji (is tanimi burada olusur)
        "niyet": _kelimeler(baslik + " " + " ".join(kullanici[:3])),
        "ozet": " ".join(_kalip_at(kullanici[0] if kullanici else baslik).split())[:180]
                or "(kalip disi icerik yok)",
        "cron": str(sid).startswith("cron_"),
    }


def adaylari_bul(gun=7, en_fazla=40):
    """Son <gun> gundeki oturumlari okur, benzerlik ciftlerini dondurur."""
    con = hkp._db()
    try:
        satirlar = con.execute(
            "SELECT session_id, COUNT(*) c, MAX(COALESCE(timestamp,0)) t "
            "FROM messages GROUP BY session_id HAVING c >= ? "
            "ORDER BY t DESC LIMIT ?", (MIN_ILET, en_fazla)).fetchall()
        if not satirlar:
            return [], []
        en_yeni = max(t for _, _, t in satirlar) or 0
        pencere = gun * 86400
        sidler = [s for s, _, t in satirlar if en_yeni - (t or 0) <= pencere]
        ozetler = [_oturum_ozeti(con, s) for s in sidler]
    finally:
        con.close()

    ciftler = []
    for i in range(len(ozetler)):
        for j in range(i + 1, len(ozetler)):
            a, b = ozetler[i], ozetler[j]
            # Iki ayri cron kosusu ayni isin devami DEGILDIR — ayni cron
            # job id'sini paylassalar bile her kosu kendi basina tamamlanir.
            if a["cron"] or b["cron"]:
                continue
            s = _jaccard(a["niyet"], b["niyet"])
            if s >= ADAY_TABAN:
                ciftler.append((s, a, b))
    ciftler.sort(key=lambda x: -x[0])
    return ozetler, ciftler


def _jev_ayni_is(ciftler, timeout=25):
    """Her aday cifti Jev'e sorar. Doner: ({(sid_a,sid_b): noul}, bilgi)."""
    key = hkp._jev_anahtar()
    if not key:
        return {}, {"hata": "anahtar_yok"}
    durum = ("Merging Hermes agent sessions. Decide whether two sessions are the "
             "SAME ongoing piece of work (so their transcripts should live in one "
             "session) or genuinely separate tasks that happen to share vocabulary.")
    sorular, esleme = {}, {}
    for n, (skor, a, b) in enumerate(ciftler):
        qid = "m%d" % n
        esleme[qid] = (a["sid"], b["sid"])
        sorular[qid] = (
            "Session A title: {!r}; first user request: {!r}.\n"
            "Session B title: {!r}; first user request: {!r}.\n"
            "Local lexical overlap: {:.2f}.\n"
            "Are A and B the same ongoing task, such that merging their transcripts "
            "into one session would help rather than confuse the reader?"
        ).format(a["baslik"][:120], a["ozet"], b["baslik"][:120], b["ozet"], skor)
    try:
        noullar = hkp._jev_batch(key, durum, sorular, timeout)
    except ValueError as e:
        return {}, {"hata": str(e)}
    return ({esleme[q]: n for q, n in noullar.items() if q in esleme},
            {"soru": len(sorular), "cevap": len(noullar)})


def gruplari_kur(ciftler, jev=False):
    """Aday ciftleri -> birlestirilebilir oturum gruplari (birlesim-bul).

    Doner: (gruplar, karar_izi). Her grup >= 2 sid iceren liste."""
    karar = []
    onayli = []
    noullar, jbilgi = ({}, None)
    if jev and ciftler:
        noullar, jbilgi = _jev_ayni_is(ciftler)

    for skor, a, b in ciftler:
        anahtar = (a["sid"], b["sid"])
        noul = noullar.get(anahtar)
        if noul is not None:
            kabul = noul >= JEV_BIRLESTIR
            kaynak = "jev"
            deger = noul
        else:
            kabul = skor >= YEREL_BIRLESTIR
            kaynak = "yerel"
            deger = skor
        karar.append({"a": a["sid"], "b": b["sid"], "kaynak": kaynak,
                      "skor": round(deger, 3), "kabul": kabul,
                      "a_ozet": a["ozet"][:80], "b_ozet": b["ozet"][:80]})
        if kabul:
            onayli.append(anahtar)

    # birlesim-bul (union-find) ile gecisli gruplama
    ust = {}

    def bul(x):
        ust.setdefault(x, x)
        while ust[x] != x:
            ust[x] = ust[ust[x]]
            x = ust[x]
        return x

    for a, b in onayli:
        ra, rb = bul(a), bul(b)
        if ra != rb:
            ust[rb] = ra

    kume = {}
    for x in list(ust):
        kume.setdefault(bul(x), []).append(x)
    gruplar = [sorted(v) for v in kume.values() if len(v) >= 2]
    return gruplar, {"kararlar": karar, "jev": jbilgi}


# --------------------------------------------------------------- birlestirme
def _imza(m):
    """Yinelenen ileti tespiti icin icerik imzasi."""
    icerik = hkp.json_metin_ayristir(m.get("content"))
    if icerik is not None and not isinstance(icerik, str):
        icerik = json.dumps(icerik, ensure_ascii=False, sort_keys=True)
    return (str(m.get("role") or ""), hkp.bosluk_temizle(icerik or "")[:4000])


def oturumlari_birlestir(sidler):
    """Birden cok oturumu kronolojik tek ileti akisina cevirir.

    - Iletler timestamp'e gore harmanlanir (timestamp yoksa oturum sirasi korunur).
    - Oturum sinirlarina gorunur ayrac eklenir; okuyan hangi daldan geldigini bilir.
    - Oturumlar arasi birebir ayni ileti bir kez tutulur (ikinci kopya atilir).
    Doner: (mesajlar, baslik, rapor)."""
    if len(sidler) < 2:
        raise ValueError("birlestirme icin en az 2 oturum gerekir")
    kaynaklar = []
    for s in sidler:
        sid, mesajlar, baslik = hkp.oturum_oku(s)
        kaynaklar.append({"sid": sid, "mesaj": mesajlar, "baslik": baslik or sid})
    # en cok iletisi olan oturumun basligi birlesik pakete ad olur
    ana = max(kaynaklar, key=lambda k: len(k["mesaj"]))

    dizili = []
    for sira, k in enumerate(kaynaklar):
        for yerel, m in enumerate(k["mesaj"]):
            t = m.get("timestamp") or 0
            dizili.append((t, sira, yerel, k, m))
    # timestamp birincil, oturum-ici sira ikincil -> oturum ici akis hic bozulmaz
    dizili.sort(key=lambda x: (x[0], x[1], x[2]))

    cikti, gorulen, onceki_sid = [], set(), None
    yinelenen = 0
    for _, _, _, k, m in dizili:
        if k["sid"] != onceki_sid:
            cikti.append({
                "role": "system",
                "content": "── OTURUM SINIRI: {} ({}) ──".format(
                    k["baslik"], k["sid"]),
            })
            onceki_sid = k["sid"]
        im = _imza(m)
        if im[1] and im in gorulen:
            yinelenen += 1
            continue
        if im[1]:
            gorulen.add(im)
        cikti.append(m)

    rapor = {
        "oturum": len(kaynaklar),
        "kaynak_ilet": sum(len(k["mesaj"]) for k in kaynaklar),
        "birlesik_ilet": len(cikti),
        "yinelenen_atilan": yinelenen,
        "ayrinti": [{"sid": k["sid"], "baslik": k["baslik"],
                     "ilet": len(k["mesaj"])} for k in kaynaklar],
    }
    baslik = "{} (+{} oturum birlesik)".format(ana["baslik"], len(kaynaklar) - 1)
    return cikti, baslik, rapor


# ============================================================ pasife alma
# state.db'ye YAZAN tek yer burasi. Hermes'in kendi API'leriyle ayni SQL:
#   end_session()          -> UPDATE sessions SET ended_at, end_reason ...
#   set_session_archived() -> archived = 1
# Ikisi de geri alinabilir (reopen_session / set_session_archived(False)) ve
# HICBIR ileti silinmez — oturum yalnizca listeden gizlenir.
GERIAL_DIZIN = "~/007-HERMES/05-CIKTILAR/oturum-paketleri/_gerial"
BEKLEME_SN = 60      # state.db yazma kilidi icin bekleme
DENEME = 5           # geri cekilmeli tekrar deneme sayisi


def pasife_al(sidler, paket_yolu, paket_id=None, sebep="czip_merge"):
    """Kaynak oturumlari kapat + arsivle. Geri alma dosyasi yazar.

    Doner: {"pasif": [...], "atlanan": [...], "gerial": "<json yolu>"}
    Yazma basarisiz olursa (kilit vb.) ISTISNA FIRLATIR — cagiran, paketi
    uretmis ama pasife alamamis oldugunu bilmeli, sessiz yarim is olmaz.
    """
    import sqlite3
    import time as _t

    yol = hkp.db_yolu()
    if not os.path.isfile(yol):
        raise ValueError("state.db yok: " + yol)
    # state.db'de canli Hermes yazma kilidini 20sn+ tutabiliyor (olculdu).
    # Tek seferlik BEGIN IMMEDIATE bu yuzden 'database is locked' ile duser;
    # busy_timeout + geri cekilmeli tekrar deneme ile bekliyoruz. Yazilan sey
    # birkac UPDATE, yani kilidi biz uzun tutmuyoruz.
    con = sqlite3.connect(yol, timeout=BEKLEME_SN)
    con.execute("PRAGMA busy_timeout = %d" % (BEKLEME_SN * 1000))
    pasif, atlanan = [], []
    onceki = {}
    try:
        kolonlar = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        arsiv_var = "archived" in kolonlar
        son_hata = None
        for deneme in range(DENEME):
            try:
                con.execute("BEGIN IMMEDIATE")
                son_hata = None
                break
            except sqlite3.OperationalError as e:
                son_hata = e
                if "locked" not in str(e) and "busy" not in str(e):
                    raise
                _t.sleep(min(2 ** deneme, 8))
        if son_hata is not None:
            raise TimeoutError(
                "state.db yazma kilidi %d denemede acilmadi (%ds bekleme): %s — "
                "canli Hermes uzun bir yazma islemi tutuyor; birkac dakika sonra "
                "tekrar dene ya da 'czip birlestir ... --pasif-yok' ile paketi al."
                % (DENEME, BEKLEME_SN, son_hata))
        for sid in sidler:
            row = con.execute(
                "SELECT ended_at, end_reason%s FROM sessions WHERE id=?"
                % (", archived" if arsiv_var else ""), (sid,)).fetchone()
            if row is None:
                atlanan.append({"sid": sid, "neden": "sessions tablosunda yok"})
                continue
            onceki[sid] = {"ended_at": row[0], "end_reason": row[1],
                           "archived": (row[2] if arsiv_var else None)}
            con.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? "
                "WHERE id=? AND ended_at IS NULL", (_t.time(), sebep, sid))
            if arsiv_var:
                con.execute("UPDATE sessions SET archived=1 WHERE id=?", (sid,))
            pasif.append(sid)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    d = os.path.expanduser(GERIAL_DIZIN)
    os.makedirs(d, exist_ok=True)
    gy = os.path.join(d, "gerial-%s.json" % _t.strftime("%Y%m%d-%H%M%S"))
    with open(gy, "w", encoding="utf-8") as f:
        json.dump({"zaman": _t.strftime("%F %T"), "sebep": sebep,
                   "paket": paket_yolu, "paket_id": paket_id,
                   "pasife_alinan": pasif, "atlanan": atlanan,
                   "onceki_durum": onceki,
                   "geri_al": "czip gerial " + os.path.basename(gy)},
                  f, ensure_ascii=False, indent=1)
    return {"pasif": pasif, "atlanan": atlanan, "gerial": gy}


def geri_al(gerial_dosyasi):
    """pasife_al'in yazdigi dosyadan oturumlari eski haline dondurur."""
    import sqlite3
    p = os.path.expanduser(gerial_dosyasi)
    if not os.path.isfile(p):
        d = os.path.expanduser(GERIAL_DIZIN)
        aday = os.path.join(d, os.path.basename(gerial_dosyasi))
        if not os.path.isfile(aday):
            raise ValueError("geri alma dosyasi yok: " + gerial_dosyasi)
        p = aday
    kayit = json.load(open(p, encoding="utf-8"))
    con = sqlite3.connect(hkp.db_yolu(), timeout=BEKLEME_SN)
    con.execute("PRAGMA busy_timeout = %d" % (BEKLEME_SN * 1000))
    geri = []
    try:
        kolonlar = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        con.execute("BEGIN IMMEDIATE")
        for sid, eski in (kayit.get("onceki_durum") or {}).items():
            con.execute("UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                        (eski.get("ended_at"), eski.get("end_reason"), sid))
            if "archived" in kolonlar and eski.get("archived") is not None:
                con.execute("UPDATE sessions SET archived=? WHERE id=?",
                            (eski["archived"], sid))
            geri.append(sid)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"geri_alinan": geri, "dosya": p}


def benzerleri_bul(sid, gun=7, jev=True):
    """TEK bir oturuma benzeyen digerlerini bulur (/czip uyarisi icin).

    czip sirasinda cagrilir: "bu isi yapan baska oturumlar da var" uyarisini
    uretir. Karari Jev verir; Jev'e ulasilamazsa yerel esige duser.
    Doner: [{"sid","ozet","skor","kaynak","kabul"}] — skora gore azalan.
    """
    con = hkp._db()
    try:
        hedef = _oturum_ozeti(con, sid)
        satirlar = con.execute(
            "SELECT session_id, COUNT(*) c, MAX(COALESCE(timestamp,0)) t "
            "FROM messages GROUP BY session_id HAVING c >= ? "
            "ORDER BY t DESC LIMIT 40", (MIN_ILET,)).fetchall()
        en_yeni = max([t for _, _, t in satirlar] or [0]) or 0
        digerleri = [
            _oturum_ozeti(con, s) for s, _, t in satirlar
            if s != hedef["sid"] and en_yeni - (t or 0) <= gun * 86400
        ]
    finally:
        con.close()

    ciftler = [(_jaccard(hedef["niyet"], o["niyet"]), hedef, o)
               for o in digerleri if not o["cron"] and not hedef["cron"]]
    ciftler = [c for c in ciftler if c[0] >= ADAY_TABAN]
    if not ciftler:
        return []
    ciftler.sort(key=lambda x: -x[0])
    _, iz = gruplari_kur(ciftler, jev=jev)
    out = []
    for k in iz["kararlar"]:
        out.append({"sid": k["b"], "ozet": k["b_ozet"], "skor": k["skor"],
                    "kaynak": k["kaynak"], "kabul": k["kabul"]})
    return out
