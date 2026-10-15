# -*- coding: utf-8 -*-
"""birlestir.py — ayni isi yapan 2+ oturumu TEK .hkp paketinde birlestirir.

czip'in kardesi: czip tek oturumu sikistirir, bu modul once "bunlar gercekten
ayni is mi?" sorusunu cevaplar, sonra birlestirip tek pakete sikistirir.

Neden gerekli: Hermes'te ayni konu icin farkli zamanlarda birden cok oturum
aciliyor (telegram'dan bir tane, TUI'den bir tane, gece cron'undan bir tane).
Her biri ayri ayri buyuyor, hicbiri otekinin ne yaptigini bilmiyor, ve
state.db'de birikip sikistirma dongusunu tikiyorlar.

Karar asamalari (ucu de Clef'e sorulabilir, --jev ile):
  1) ADAY BULMA      — baslik + kullanici mesaji ortakligi (yerel, ucuz)
  2) AYNI IS MI?     — Clef noul skoru; esik alti aday elenir
  3) ARAC CIKTISI    — hkp.sikistir'in mevcut Jev budamasi (degismedi)

Karar kapısı istendiğinde erişilemiyorsa aday onaylanmaz. Tarama yalnız öneridir;
birleştirme açık kimlik seçimi gerektirir.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

import hkp

# Ayni-is esikleri
JEV_BIRLESTIR = 0.60     # Clef noul bunun ustundeyse "ayni is" sayilir
YEREL_BIRLESTIR = 0.45   # Clef kullanılmıyorsa yerel Jaccard esigi (bilerek daha sikici)
ADAY_TABAN = 0.18        # bunun altindaki cift Clef'e bile sorulmaz
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
    """Her aday cifti Clef'e sorar. Doner: ({(sid_a,sid_b): noul}, bilgi)."""
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
        noullar, motor = hkp.karar_batch(durum, sorular, timeout)
    except ValueError as e:
        return {}, {"hata": str(e)}
    guven = hkp.karar_guven()
    dusuk = {q for q, g in guven.items() if g < hkp.KARAR_MIN_GUVEN}
    return ({esleme[q]: n for q, n in noullar.items() if q in esleme and q not in dusuk},
            {"soru": len(sorular), "cevap": len(noullar), "motor": motor, "dusuk_guven": len(dusuk)})


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
            kaynak = (jbilgi or {}).get("motor", "jev")
            deger = noul
        else:
            kabul = not jev and skor >= YEREL_BIRLESTIR
            kaynak = "clef_unavailable" if jev else "yerel"
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
    return kaynaklari_birlestir(kaynaklar)


def kaynaklari_birlestir(kaynaklar):
    """Yuklenmis kaynaklari tek akisa cevirir: [{sid, mesaj, baslik}, ...].

    oturumlari_birlestir() bunun state.db'den okuyan sarmalayicisidir. Ayrilma
    sebebi: Hermes disindaki dokumler (or. Claude Code oturum disa aktarimi)
    state.db'de yoktur ama ayni birlestirme kurallarini hak eder — sinir
    isaretleri, zaman sirasi ve oturumlar arasi birebir tekrar ayiklama.
    """
    if len(kaynaklar) < 2:
        raise ValueError("birlestirme icin en az 2 kaynak gerekir")
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
                "content": hkp.T("── SESSION BOUNDARY: {} ({}) ──",
                                 "── OTURUM SINIRI: {} ({}) ──").format(
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
BEKLEME_SN = 60


def _db_identity():
    from czip_runtime import session_env
    return {"database": os.path.realpath(hkp.db_yolu()),
            "profile": os.path.realpath(os.path.expanduser(session_env("HERMES_HOME", "~/.hermes")))}


def gerial_dizin():
    return os.path.join(os.path.dirname(_db_identity()["database"]), "czip", "undo")


def pasife_al(sidler, paket_yolu, paket_id=None, sebep="czip_end"):
    """Archive exact IDs transactionally; durable, profile-bound undo precedes commit."""
    import time
    import uuid
    from czip_runtime import atomic_json, T
    identity = _db_identity()
    if not os.path.isfile(identity["database"]):
        raise ValueError(T("Database missing", "Veritabanı bulunamadı"))
    if not os.path.isfile(os.path.expanduser(paket_yolu)):
        raise ValueError(T("Verified package required", "Doğrulanmış paket gerekli"))
    undo = os.path.join(gerial_dizin(), "undo-" + uuid.uuid4().hex + ".json")
    previous, after, skipped = {}, {}, []
    con = sqlite3.connect(identity["database"], timeout=BEKLEME_SN)
    try:
        con.execute("BEGIN IMMEDIATE")
        archived = "archived" in {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        for sid in dict.fromkeys(sidler):
            row = con.execute("SELECT ended_at,end_reason" + (",archived" if archived else "") +
                              " FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                skipped.append({"sid":sid,"neden":T("Session missing", "Oturum bulunamadı")}); continue
            old = {"ended_at":row[0], "end_reason":row[1], "archived":row[2] if archived else None}
            new = {"ended_at":row[0] if row[0] is not None else time.time(),
                   "end_reason":row[1] if row[0] is not None else sebep,
                   "archived":1 if archived else None}
            previous[sid], after[sid] = old, new
            con.execute("UPDATE sessions SET ended_at=?,end_reason=? WHERE id=?", (new["ended_at"],new["end_reason"],sid))
            if archived: con.execute("UPDATE sessions SET archived=1 WHERE id=?", (sid,))
        atomic_json(undo, {"version":2, **identity, "paket":paket_yolu, "paket_id":paket_id,
                           "onceki_durum":previous, "uygulanan_durum":after})
        con.commit()
    except BaseException:
        con.rollback()
        if os.path.exists(undo): os.unlink(undo)
        raise
    finally:
        con.close()
    return {"pasif":list(previous), "atlanan":skipped, "gerial":undo}


def geri_al(gerial_dosyasi):
    """Restore only this database/profile and only unchanged archived records."""
    from czip_runtime import file_lock, read_json, T
    p = os.path.expanduser(gerial_dosyasi)
    if not os.path.isfile(p): p = os.path.join(gerial_dizin(), os.path.basename(p))
    with file_lock(p):
        record = read_json(p, {})
        if record.get("version") != 2 or any(record.get(k) != v for k,v in _db_identity().items()):
            raise ValueError(T("Undo belongs to another profile/database or lacks identity", "Geri alma başka profil/veritabanına ait veya kimliği eksik"))
        previous = record.get("onceki_durum") or {}
        applied = record.get("uygulanan_durum") or {}
        if set(previous) != set(applied): raise ValueError("Invalid undo record")
        con = sqlite3.connect(hkp.db_yolu(), timeout=BEKLEME_SN)
        try:
            con.execute("BEGIN IMMEDIATE")
            archived = "archived" in {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
            for sid, expected in applied.items():
                row = con.execute("SELECT ended_at,end_reason" + (",archived" if archived else "") + " FROM sessions WHERE id=?", (sid,)).fetchone()
                actual = {"ended_at":row[0],"end_reason":row[1],"archived":row[2] if archived else None} if row else None
                if actual != expected:
                    raise ValueError(T("Session changed since archiving: ", "Arşivlemeden sonra oturum değişmiş: ") + sid)
            for sid, old in previous.items():
                con.execute("UPDATE sessions SET ended_at=?,end_reason=? WHERE id=?", (old["ended_at"],old["end_reason"],sid))
                if archived: con.execute("UPDATE sessions SET archived=? WHERE id=?", (old["archived"],sid))
            con.commit()
        except BaseException:
            con.rollback(); raise
        finally:
            con.close()
    return {"geri_alinan":list(previous), "dosya":p}


def benzerleri_bul(sid, gun=7, jev=True):
    """TEK bir oturuma benzeyen digerlerini bulur (/czip uyarisi icin).

    czip sirasinda cagrilir: "bu isi yapan baska oturumlar da var" uyarisini
    uretir. Karari Jev verir; Clef'e ulasilamazsa yerel esige duser.
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
