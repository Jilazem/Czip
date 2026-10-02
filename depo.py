# -*- coding: utf-8 -*-
"""depo.py — one searchable store for every .hkp package (czip long-term memory).

Why: `czip arsivara` opens and LZMA-decodes every package on each query. That is
fine for 20 packages and hopeless for 2000. This builds a SQLite FTS5 index once,
then answers in milliseconds and keeps working long after the sessions are gone.

Design notes
  - Incremental: a package is re-indexed only when its mtime/size changes.
  - The packages stay the source of truth; the index is a disposable derivative
    (delete it and `czip index` rebuilds it).
  - Indexes the FULL message text (CZIP_INDEX_CHARS caps it if you need a smaller
    store). A partial index is a memory that forgets without telling you.
    Packages stay the source of truth; `czip range` still returns exact content.
  - Tokenizer: unicode61 with Turkish characters folded, so ' İMAR' finds 'imar'.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import unicodedata

import hkp

DEPO_YOLU = "~/007-HERMES/05-CIKTILAR/oturum-paketleri/czip-index.db"
# Full message text is indexed. An earlier 400-char snippet cap made the store
# fast and small but SILENTLY UNSEARCHABLE past the cap: "database is locked"
# returned 0 hits although it appears in dozens of tool outputs. A memory that
# quietly forgets is worse than a bigger file, so the cap is now generous and
# configurable. FTS5 content is stored once; packages remain the source of truth.
PARCA = int(os.environ.get("CZIP_INDEX_CHARS", "0")) or None   # None = full text
# STAIR (arXiv 2609.03874): mesajlar sabit genişlikli BLOKLARA gruplanır; blok
# metni üye mesajların birleşimi olduğu için, blok-içi dağınık terimler blok
# katmanında eşleşir ama tek mesaj katmanında 0 kalır — iki aşamalı arama bu
# recall açığını kapatır. Blok kimliği idx'ten türetilir (idx // BLOK): ayrı
# eşleme tablosu gerekmez, mesaj -> blok geçişi her zaman tutarlıdır.
BLOK = max(2, int(os.environ.get("CZIP_BLOK_ILET", "8")))
# Blok metni tavanı: FTS boyutunu makul tutmak için blok başına karakter sınırı.
BLOK_METIN = int(os.environ.get("CZIP_BLOK_METIN", "60000")) or None
SEMA_SURUMU = 2   # 1 = yalnız mesaj, 2 = +blok katmanı (bfts)
KUR = """
CREATE TABLE IF NOT EXISTS packages (
    path      TEXT PRIMARY KEY,
    kid       TEXT,
    title     TEXT,
    mtime     REAL,
    size      INTEGER,
    msgs      INTEGER,
    indexed_at REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS mfts USING fts5(
    path UNINDEXED,
    idx  UNINDEXED,
    role UNINDEXED,
    text,
    tokenize = "unicode61 remove_diacritics 2"
);
CREATE VIRTUAL TABLE IF NOT EXISTS bfts USING fts5(
    path UNINDEXED,
    bid  UNINDEXED,
    bas  UNINDEXED,
    son  UNINDEXED,
    ozet UNINDEXED,
    text,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def yol():
    return os.path.expanduser(DEPO_YOLU)


def _ac():
    d = yol()
    os.makedirs(os.path.dirname(d), exist_ok=True)
    con = sqlite3.connect(d, timeout=30)
    con.executescript(KUR)
    return con


def _paketler():
    # realpath: ~/007-HERMES ile /Volumes/EX/007-HERMES-M4-LIVE AYNI dizindir
    # (symlink). Normalize edilmezse aynı paket iki ayrı yol dizesiyle iki satır
    # olur — indeks şişer, arama aynı paketi iki kez listeler (26.09 dedup fix).
    d = os.path.realpath(os.path.expanduser(hkp.PAKET_DIZIN))
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".hkp")),
                  key=os.path.getmtime, reverse=True)


def _kid_haritasi():
    """short package id -> path, from czip's own registry (best effort)."""
    out = {}
    try:
        kayit = hkp._kayit_oku()
    except Exception:
        return out
    if not isinstance(kayit, dict):
        return out
    for k, v in kayit.items():
        y = v.get("yol") if isinstance(v, dict) else v
        if y:
            # realpath: kayit.json'daki yollar abspath ile yazılır (symlink ÇÖZÜLMEZ),
            # ama _paketler() 26.09 dedup fix'iyle realpath kullanır. ~/007-HERMES
            # gibi symlink'li yollarda abspath≠realpath → kid EŞLEŞMEZ, canlıda
            # kid=None kalır ve 'czip range' ipucu sessizce düşer. Her iki taraf da
            # normalize edilmeli (test_stair 26.09 kök-nedeni).
            out[os.path.realpath(os.path.expanduser(str(y)))] = k
    return out


def guncelle(tam=False, ilerleme=None):
    """Index new/changed packages. Returns a summary dict.

    Şema sürümü gerideyse (ör. blok katmanı bfts yeni eklendi) salt-mtime skip
    YANLIŞ atlar — eski paketlerde bfts satırı olmaz, iki aşamalı arama onları
    sessizce kaybeder. Bu yüzden sürüm düşükse tek seferlik TAM yeniden indeks
    koşar ve user_version yükseltilir.
    """
    con = _ac()
    kidler = _kid_haritasi()
    if con.execute("PRAGMA user_version").fetchone()[0] < SEMA_SURUMU:
        tam = True
    mevcut = {r[0]: (r[1], r[2]) for r in
              con.execute("SELECT path, mtime, size FROM packages")}
    yeni = guncellenen = atlanan = ileti = 0
    try:
        for p in _paketler():
            st = os.stat(p)
            onceki = mevcut.get(p)
            if not tam and onceki and abs(onceki[0] - st.st_mtime) < 1 and onceki[1] == st.st_size:
                atlanan += 1
                continue
            try:
                meta, kayitlar = hkp.yukle(p)
            except Exception:
                continue
            sozluk = meta.get("soz", [])
            con.execute("DELETE FROM mfts WHERE path = ?", (p,))
            con.execute("DELETE FROM bfts WHERE path = ?", (p,))
            satir = []
            bloklar = {}   # bid -> [(idx, metin), ...] — yalnız indekslenen mesajlar
            for i, k in enumerate(kayitlar):
                t = hkp.coz_sozluk(str(k.get("c") or ""), sozluk)
                if not t:
                    continue
                t = " ".join(t.split())
                if PARCA:
                    t = t[:PARCA]
                satir.append((p, i, str(k.get("r") or "?"), t))
                bloklar.setdefault(i // BLOK, []).append((i, t))
            con.executemany("INSERT INTO mfts(path, idx, role, text) VALUES (?,?,?,?)", satir)
            ileti += len(satir)
            # STAIR blok satırları: metin = üye mesajların birleşimi; aralık =
            # ilk/son İNDEKSLENMİŞ üye mesaj idx'i — her zaman 0..msgs-1, uydurma
            # id üretilemez (24.09 constrained-retrieval sözleşmesi korunur).
            bsat = []
            for bid in sorted(bloklar):
                uyeler = bloklar[bid]
                birlesim = " ".join(t for _, t in uyeler)
                if BLOK_METIN:
                    birlesim = birlesim[:BLOK_METIN]
                bsat.append((p, bid, uyeler[0][0], uyeler[-1][0],
                             uyeler[0][1][:200], birlesim))
            con.executemany(
                "INSERT INTO bfts(path, bid, bas, son, ozet, text) VALUES (?,?,?,?,?,?)", bsat)
            con.execute(
                "INSERT INTO packages(path,kid,title,mtime,size,msgs,indexed_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "kid=excluded.kid,title=excluded.title,mtime=excluded.mtime,"
                "size=excluded.size,msgs=excluded.msgs,indexed_at=excluded.indexed_at",
                (p, kidler.get(os.path.realpath(p)),
                 # meta anahtarı 'baslik'tır (paketle); 'b' hiç yazılmaz — eski
                 # 'b' okumasu başlığı HER ZAMAN dosya adına düşürüyordu
                 # (test_stair 26.09).
                 meta.get("b") or meta.get("baslik") or os.path.basename(p),
                 st.st_mtime, st.st_size, len(kayitlar), time.time()))
            if onceki:
                guncellenen += 1
            else:
                yeni += 1
            if ilerleme:
                ilerleme(p, len(satir))
        con.execute("PRAGMA user_version = %d" % SEMA_SURUMU)
        # Öksüz satır temizliği: dizinden silinen VEYA normalize öncesi (abspath)
        # yazılmış eski yol satırları. 26.09 realpath fix'i aynı paketi yeni yol
        # dizesiyle yeniden ekledi; eski satır hiçbir zaman güncellenmez çünkü
        # _paketler() artık yalnız realpath üretir — 'czip arsivara' aynı paketi
        # kid'li + kid'siz İKİ KEZ listeliyordu (canlı kanıt: 26.09 sp98e6 çift
        # satır). Kaynak = dosya sistemi: dizinde olmayan path silinir.
        gecerli = set(_paketler())
        ozgur = [r[0] for r in con.execute("SELECT path FROM packages")
                 if r[0] not in gecerli]
        for p in ozgur:
            con.execute("DELETE FROM packages WHERE path = ?", (p,))
            con.execute("DELETE FROM mfts WHERE path = ?", (p,))
            con.execute("DELETE FROM bfts WHERE path = ?", (p,))
        con.commit()
    finally:
        con.close()
    return {"yeni": yeni, "guncellenen": guncellenen, "atlanan": atlanan,
            "ileti": ileti, "depo": yol(),
            "boyut": os.path.getsize(yol()) if os.path.exists(yol()) else 0}


# Turkce baglaclik/edat sozcukler — arama terimi olarak anlam TASIMAZ.
# DIKKAT (denetim bulgusu 21.09): anlamli olabilen kelimeler (kontrol, once,
# sonra, var, yok, az, zaman, yeni...) BILEREK listede YOK — yanlis-0 uretmektense
# gurultulu isabet yegdir. Duz kucuk harf, diyakritiksiz karsilastirma.
_DURAG = {
    "ile", "icn", "için", "ve", "veya", "yahut", "ancak",
    "bir", "bi", "bu", "su", "şu", "de", "da", "ki", "mi", "mı", "mu", "mü",
    "ne", "nasıl", "nasil", "neden", "niye", "niçin", "nicin", "gibi", "kadar",
    "daha", "cok", "çok", "hep", "hic", "hiç",
    "boyle", "böyle", "soyle", "şöyle",
    "nereye", "nerede", "nereden", "kim", "hangi", "kaç", "misin",
    "idim", "idi", "iz", "dir", "dır", "midir",
    "yapilir", "yapılır", "yapildi", "yapıldı", "yapiyor", "yapıyor",
    "yaptim", "yaptım",
}


def _terimler(sorgu):
    """Sorguyu arama terimlerine bölür: cümle değil, kelime kelime FTS terimi.

    Neden: FTS5 tam ifade (phrase) aramasında '"mobil imza ile ... yapılır"' tüm
    cümlenin ardışık geçmesini ister → pratikte hep 0. Kelimeleri ayırınca
    'VE' bağlanan terim kümeleri gerçek anlamı taşır.
    """
    ham = re.findall(r"[^\W\d_]+", str(sorgu), re.UNICODE)
    gor, terim = set(), []
    for w in ham:
        a = _katlas(w)
        if len(a) < 2 or a in _durag_kumle():
            continue
        if a in gor:
            continue
        gor.add(a)
        terim.append(w)
    return terim or ham  # her sey duraksa yine de terimlerle ara (sessiz 0 yasak)


def _katlas(w):
    """Kucuk harf + diyakritik katlamasi — FTS5 unicode61 remove_diacritics 2
    ile ayni davranis (İ→i, ş→s, ğ→g, ü→u).
    DIKKAT (denetim 21.09): ı→i BILEREK katlanmaz — FTS 'ımza' ile 'imza'yı
    ayri terim sayar; burada katlamak yanlis-0 uretir (olculdu: 1222 -> 0)."""
    s = unicodedata.normalize("NFD", str(w).lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _durag_kumle():
    return {_katlas(x) for x in _DURAG}


def _esleme(q):
    """FTS5 deyimi üretir; her terim ayrı tırnaklı, VE ile bağlı."""
    return " AND ".join('"' + str(t).replace('"', '""') + '"' for t in q)


def ara(sorgu, limit=25, paket_limit=3, blok=True):
    """FTS search across every indexed package. Returns grouped hits.

    Kelime kelime + merdiven: önce tüm anlamlı terimler, bulamazsa en nadir
    3, sonra 2, sonra 1 terim. Hangi basamakta bulunduğu `not` ile döner —
    böylece uzun Türkçe cümleler sessizce '0 isabet' üretmez.

    STAIR iki aşamalı arama (arXiv 2609.03874; blok=True varsayılan): mesaj
    katmanı merdiveni 'tek terim'e kadar gerilerse tam sorgu hiçbir TEK mesajda
    tam geçmiyor demektir; terimler AYNI BLOKTA birlikte geçebilir. O zaman blok
    katmanı (bfts) tam sorguyla denenir ve bulunan blok aralıkları 'blok' olarak
    EKLENİR — mevcut mesaj isabetleri asla silinmez (eski davranış korunur).
    blok=False eski tek katmanlı aramadır (karşılaştırma/geri alma anahtarı).
    """
    d = yol()
    if not os.path.exists(d):
        raise ValueError("index yok — once `czip index` calistir")
    con = sqlite3.connect("file:%s?mode=ro" % d, uri=True, timeout=15)
    try:
        terim = _terimler(sorgu)

        def say(q):
            return con.execute(
                "SELECT COUNT(*) FROM mfts WHERE mfts MATCH ?", (_esleme(q),)
            ).fetchone()[0]

        def satirlar(q):
            return con.execute(
                "SELECT m.path, m.idx, m.role, snippet(mfts, 3, '<', '>', '…', 18), "
                "       p.kid, p.title, p.mtime, p.msgs "
                "FROM mfts m JOIN packages p ON p.path = m.path "
                "WHERE mfts MATCH ? ORDER BY p.mtime DESC LIMIT ?",
                (q, limit * paket_limit)).fetchall()

        # Tam ifade (ardisik) hâlâ en iyi sinyal — varsa o kazanır.
        butun = con.execute(
            "SELECT m.path, m.idx, m.role, snippet(mfts, 3, '<', '>', '…', 18), "
            "       p.kid, p.title, p.mtime, p.msgs "
            "FROM mfts m JOIN packages p ON p.path = m.path "
            "WHERE mfts MATCH ? ORDER BY p.mtime DESC LIMIT ?",
            ('"' + str(sorgu).replace('"', '""') + '"', limit * paket_limit)).fetchall()
        butun_toplam = con.execute(
            "SELECT COUNT(*) FROM mfts WHERE mfts MATCH ?",
            ('"' + str(sorgu).replace('"', '""') + '"',)).fetchone()[0]

        rows, toplam, not_, basamak = [], 0, "", ""
        blok_satirlar, blok_toplam, blok_ad = [], 0, ""
        if butun_toplam:
            rows, toplam = butun, butun_toplam
            basamak = "tam ifade"
        elif terim:
            # Nadirlik sirasi: seyre terim once — ayirt edici olan o.
            # Cache + tavan (IADE duzeltmesi 21.09): her terim BIR KEZ sayilir;
            # 'yok' teshisi de ayni cache'i kullanir → MATCH sayisi N ile degil
            # tavani asan sabitle buyur (eski: 200 terim = 214 MATCH).
            # Hata artik yutulmaz (siradaki except kaldirildi — yanlış-negatif
            # yerine gorunur hata).
            adetle = {}
            def adet(t):
                if t not in adetle:
                    adetle[t] = say([t])
                return adetle[t]
            denenecek = []
            if len(terim) == 1:
                # Tek anlamlı terim 'merdiven' degil, sorgunun kendisidir —
                # dogrudan aranir (IADE duzeltmesi: 'Ile imza' yanlis-0 donerdü,
                # oysa 'imza' tek basina 1222).
                denenecek.append((terim[0], "tek terim"))
            else:
                sirali = sorted(terim[:8], key=adet)
                denenecek.append((" ".join(terim[:8]), "tüm terimler VE"))
                for n in (3, 2):
                    if len(sirali) > n:
                        denenecek.append((" ".join(sirali[:n]),
                                          "en ayirt edici %d terim" % n))
                # Son cares: tek terim — konu bagini kaybeder, dusuk guven
                # etiketiyle (Sol denetimi 21.09).
                denenecek.append((sirali[0], "tek terim (düşük güven)"))
            for i, (qs, ad) in enumerate(denenecek):
                q = _esleme(qs.split())
                toplam = con.execute(
                    "SELECT COUNT(*) FROM mfts WHERE mfts MATCH ?", (q,)).fetchone()[0]
                if toplam:
                    rows = satirlar(q)
                    basamak = ad
                    # 'tüm terimler VE' beklenen sonuctur, not istemez;
                    # 'tek terim' ise sorgunun yarisi demektir — sezdir.
                    if i or ad == "tek terim":
                        not_ = ("ifadenin tamamı eşleşmedi → '%s' ile bulundu "
                                "(sorgu: %s)" % (ad, qs))
                    break
            if not toplam:
                # Teshis yalniz degerlendirilen terimlerden (cache'li, <=8+1).
                tanilan = terim if len(terim) <= 8 else terim[:8]
                yok = [t for t in tanilan if adet(t) == 0]
                not_ = ("eşleşme yok. Hiçbir pakette geçmeyen terimler: %s | "
                        "denenen: %s" % (", ".join(yok) or "-",
                                         "; ".join(a for _, a in denenecek)))
            # ---- STAIR 2. aşama: blok katmanı ------------------------------------
            # Koşul: birden çok terim VE mesaj katmanı yalnız 'tek terim'
            # basamağında buldu (ya da hiç bulamadı). Tam sorgu tek mesajda yok;
            # bloğun birleşik metninde varsa konu/blok önerisi recall açığını
            # kapatır. Tek terimli sorguda blok araması ek bilgi taşımaz → atla.
            if blok and len(terim) > 1 and (not basamak or basamak.startswith("tek terim")):
                bh = _blok_ara(con, terim, limit, paket_limit)
                if bh:
                    blok_satirlar, blok_toplam, blok_ad = bh
                    if not basamak:
                        # 0-isabet durumunu blok bulgusuyla ZENGİNLEŞTİR, sessiz
                        # 0 yasak — teshis notu korunur, blok önerisi eklenir.
                        basamak = "blok (iki aşamalı)"
                    else:
                        basamak += " + blok (iki aşamalı)"
                    not_ = (not_ + " | " if not_ else "") + \
                        ("tam sorgu blok katmanında eşleşti: %d blok (%s) — "
                         "blok aralığını `czip aralik` ile oku" % (blok_toplam, blok_ad))
        else:
            not_ = "arama terimi bulunamadı (boş ya da anlamsız girdi)"
    finally:
        con.close()
    # STAIR: yalniz gecerli yapi id'leri (0..msgs-1). Uydurma i uretilmesin.
    grup, sira = {}, []
    for path, idx, role, snip, kid, title, mtime, msgs in rows:
        n = int(msgs or 0)
        g = grup.setdefault(path, {
            "kid": kid, "title": title, "mtime": mtime,
            "msgs": n, "aralik": "0-%d" % (n - 1) if n else "yok", "hits": [],
        })
        if path not in sira:
            sira.append(path)
        if n and (idx < 0 or idx >= n):
            continue  # aralik disi i — constrained retrieval
        if len(g["hits"]) < paket_limit:
            g["hits"].append({"i": idx, "role": role, "snippet": snip})
    # Blok önerileri: path bazlı grupla, mesaj gruplarına EKLENİR (silme yok).
    # bas/son zaten indekslenmiş üye idx'leri olduğundan 0..msgs-1 garantili;
    # yine de 24.09 sözleşmesi gereği msgs sınırıyla doğrulanır.
    for path, bid, bas, son, snip, kid, title, mtime, msgs in blok_satirlar:
        n = int(msgs or 0)
        g = grup.setdefault(path, {
            "kid": kid, "title": title, "mtime": mtime,
            "msgs": n, "aralik": "0-%d" % (n - 1) if n else "yok", "hits": [],
        })
        if path not in sira:
            sira.append(path)
        if n and (bas < 0 or son >= n):
            continue  # aralik disi blok — uydurma aralik gösterme
        g.setdefault("blok", [])
        if len(g["blok"]) < paket_limit:
            g["blok"].append({"i": "%d-%d" % (bas, son), "aralik": "%d-%d" % (bas, son),
                              "snippet": snip})
    return {"total": toplam, "packages": [dict(grup[p], path=p) for p in sira[:limit]],
            "basamak": basamak, "not": not_, "terim": terim}


def _blok_ara(con, terim, limit, paket_limit):
    """STAIR blok katmanı araması: tam ifade → tüm terimler VE (blok birleşimi).

    Merdivenin '3/2/1 terim' basamakları BILEREK yok: mesaj katmanı onları zaten
    denedi ve tek-alt-küme eşleşmesi konu bağını yeniden kaybeder — blok katmanı
    TAM sorguyu anlatan yapıyı bulmak için vardır. Dönüş: (satırlar, toplam, ad)
    ya da None. bfts yoksa (eski şema) tablo hatası yutulur → None (sessiz 0
    değil: ara()'nin teshis notu zaten 'eşleşme yok' der).
    """
    sec = ("SELECT b.path, b.bid, b.bas, b.son, "
           "       snippet(bfts, 5, '<', '>', '…', 18), "
           "       p.kid, p.title, p.mtime, p.msgs "
           "FROM bfts b JOIN packages p ON p.path = b.path "
           "WHERE bfts MATCH ? ORDER BY p.mtime DESC LIMIT ?")
    try:
        # 1) Sorgu terimleri blokta ARDIŞIK geçiyorsa en güçlü sinyal.
        ham = '"' + " ".join(str(t) for t in terim).replace('"', '""') + '"'
        n = con.execute("SELECT COUNT(*) FROM bfts WHERE bfts MATCH ?",
                        (ham,)).fetchone()[0]
        if n:
            return con.execute(sec, (ham, limit * paket_limit)).fetchall(), n, "tam ifade (blok)"
        # 2) Terimler blok birleşiminde birlikte geçiyor (sırasız) — STAIR asıl
        #    kazancı burada: tek mesajda DAĞINIK olan konu, blok bir aralıkta tam.
        q = _esleme(terim[:8])
        n = con.execute("SELECT COUNT(*) FROM bfts WHERE bfts MATCH ?",
                        (q,)).fetchone()[0]
        if n:
            return con.execute(sec, (q, limit * paket_limit)).fetchall(), n, "tüm terimler VE (blok)"
    except sqlite3.OperationalError:
        return None   # eski indeks: bfts yok — iki aşamalı arama devre dışı
    return None


def istatistik():
    d = yol()
    if not os.path.exists(d):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % d, uri=True, timeout=10)
    try:
        pk, msj = con.execute("SELECT COUNT(*), COALESCE(SUM(msgs),0) FROM packages").fetchone()
        idx = con.execute("SELECT COUNT(*) FROM mfts").fetchone()[0]
        en_eski, en_yeni = con.execute(
            "SELECT MIN(mtime), MAX(mtime) FROM packages").fetchone()
    finally:
        con.close()
    return {"packages": pk, "messages": msj, "indexed": idx, "bytes": os.path.getsize(d),
            "oldest": en_eski, "newest": en_yeni, "path": d}
