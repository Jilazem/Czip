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
import sqlite3
import time

import hkp

DEPO_YOLU = "~/007-HERMES/05-CIKTILAR/oturum-paketleri/czip-index.db"
# Full message text is indexed. An earlier 400-char snippet cap made the store
# fast and small but SILENTLY UNSEARCHABLE past the cap: "database is locked"
# returned 0 hits although it appears in dozens of tool outputs. A memory that
# quietly forgets is worse than a bigger file, so the cap is now generous and
# configurable. FTS5 content is stored once; packages remain the source of truth.
PARCA = int(os.environ.get("CZIP_INDEX_CHARS", "0")) or None   # None = full text
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
    d = os.path.expanduser(hkp.PAKET_DIZIN)
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
            out[os.path.abspath(os.path.expanduser(str(y)))] = k
    return out


def guncelle(tam=False, ilerleme=None, butce_sn=None):
    """Index new/changed packages. Returns a summary dict.

    butce_sn: stop after this many seconds (hooks must stay fast); the rest is
    picked up by the next call because unchanged packages are skipped.
    Packages that no longer exist (cleanup moved them) are dropped from the index."""
    t0 = time.time()
    con = _ac()
    kidler = _kid_haritasi()
    mevcut = {r[0]: (r[1], r[2]) for r in
              con.execute("SELECT path, mtime, size FROM packages")}
    yeni = guncellenen = atlanan = ileti = silinen = 0
    kesildi = False
    try:
        for p in list(mevcut):
            if not os.path.exists(p):
                con.execute("DELETE FROM mfts WHERE path = ?", (p,))
                con.execute("DELETE FROM packages WHERE path = ?", (p,))
                silinen += 1
        for p in _paketler():
            if butce_sn is not None and time.time() - t0 > butce_sn:
                kesildi = True
                break
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
            satir = []
            for i, k in enumerate(kayitlar):
                t = hkp.coz_sozluk(str(k.get("c") or ""), sozluk)
                if not t:
                    continue
                t = " ".join(t.split())
                if PARCA:
                    t = t[:PARCA]
                satir.append((p, i, str(k.get("r") or "?"), t))
            con.executemany("INSERT INTO mfts(path, idx, role, text) VALUES (?,?,?,?)", satir)
            ileti += len(satir)
            con.execute(
                "INSERT INTO packages(path,kid,title,mtime,size,msgs,indexed_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "kid=excluded.kid,title=excluded.title,mtime=excluded.mtime,"
                "size=excluded.size,msgs=excluded.msgs,indexed_at=excluded.indexed_at",
                (p, kidler.get(os.path.abspath(p)), meta.get("baslik") or meta.get("b") or os.path.basename(p),
                 st.st_mtime, st.st_size, len(kayitlar), time.time()))
            if onceki:
                guncellenen += 1
            else:
                yeni += 1
            if ilerleme:
                ilerleme(p, len(satir))
        con.commit()
    finally:
        con.close()
    return {"yeni": yeni, "guncellenen": guncellenen, "atlanan": atlanan,
            "silinen": silinen, "kesildi": kesildi,
            "ileti": ileti, "depo": yol(),
            "boyut": os.path.getsize(yol()) if os.path.exists(yol()) else 0}


def ara(sorgu, limit=25, paket_limit=3):
    """FTS search across every indexed package. Returns grouped hits."""
    d = yol()
    if not os.path.exists(d):
        raise ValueError("index yok — once `czip index` calistir")
    con = sqlite3.connect("file:%s?mode=ro" % d, uri=True, timeout=15)
    try:
        # FTS5 MATCH: quote the query so punctuation cannot break the syntax.
        q = '"' + str(sorgu).replace('"', '""') + '"'
        rows = con.execute(
            "SELECT m.path, m.idx, m.role, snippet(mfts, 3, '<', '>', '…', 18), "
            "       p.kid, p.title, p.mtime "
            "FROM mfts m JOIN packages p ON p.path = m.path "
            "WHERE mfts MATCH ? ORDER BY p.mtime DESC LIMIT ?",
            (q, limit * paket_limit)).fetchall()
        toplam = con.execute(
            "SELECT COUNT(*) FROM mfts WHERE mfts MATCH ?", (q,)).fetchone()[0]
    finally:
        con.close()
    grup, sira = {}, []
    for path, idx, role, snip, kid, title, mtime in rows:
        g = grup.setdefault(path, {"kid": kid, "title": title, "mtime": mtime, "hits": []})
        if path not in sira:
            sira.append(path)
        if len(g["hits"]) < paket_limit:
            g["hits"].append({"i": idx, "role": role, "snippet": snip})
    return {"total": toplam, "packages": [dict(grup[p], path=p) for p in sira[:limit]]}


_DUR = {"için", "icin", "olarak", "şimdi", "simdi", "bunu", "şunu", "sunu", "nasıl",
        "nasil", "neden", "gibi", "daha", "sonra", "önce", "once", "kadar", "that",
        "this", "with", "from", "what", "have", "please", "lütfen", "lutfen", "yap",
        "yapar", "misin", "mısın", "olsun", "istiyorum", "bana", "bizim", "the", "and"}


def _dur_k():
    return {katla(w) for w in _DUR}


def katla(metin):
    """Karsilastirma icin: kucuk harf + Turkce/aksan katlama (eşiği == esigi).
    FTS5 'remove_diacritics 2' ile ayni davranis; alaka kapisi da ayni gozle bakar."""
    import unicodedata
    t = str(metin).replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", t)
                   if not unicodedata.combining(c))


def anahtar_kelimeler(metin, azami=8):
    """Serbest metin -> FTS icin anlamli kelimeler (uzunluk + nadirlik sirasi)."""
    import re
    global _DUR_K
    if _DUR_K is None:
        _DUR_K = _dur_k()
    kel = []
    for w in re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü_]{4,}", str(metin)):
        w = katla(w)
        if w not in _DUR_K and w not in kel and not w.isdigit():
            kel.append(w)
    kel.sort(key=len, reverse=True)
    return kel[:azami]


_DUR_K = None


def hatirla(metin, limit=3, haric=(), esik=0.5, haric_sid=None):
    """RAG hatirlama: serbest metne (kullanici istegi) en ilgili gecmis iletiler.

    `ara` tam ifade arar; bu ise kelimelerden herhangi birini (OR) bm25 ile
    siralar, sonra ALAKA KAPISI uygular: sorgu kelimelerinin en az `esik`
    orani (ve en az 2'si) isabet metninde gecmeli. Alakasiz hatirlatma,
    hic hatirlatmamaktan kotudur — baglami bosuna sisirir.
    Doner: [{kid, path, title, i, role, snippet, skor}] (en fazla `limit`)."""
    global _DUR_K
    if _DUR_K is None:
        _DUR_K = _dur_k()
    d = yol()
    kel = anahtar_kelimeler(metin)
    if not os.path.exists(d) or len(kel) < 2:
        return []
    q = " OR ".join('"%s"' % w.replace('"', '') for w in kel)
    con = sqlite3.connect("file:%s?mode=ro" % d, uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT m.path, m.idx, m.role, m.text, "
            "       snippet(mfts, 3, '', '', '…', 24), p.kid, p.title, bm25(mfts) "
            "FROM mfts m JOIN packages p ON p.path = m.path "
            "WHERE mfts MATCH ? AND m.role IN ('user', 'assistant') "
            "ORDER BY bm25(mfts) LIMIT 200", (q,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    gerekli = max(2, int(len(kel) * esik + 0.999))
    out, paketler, sid_onbellek, metinler = [], set(), {}, set()
    for path, idx, role, text, snip, kid, title, skor in rows:
        if path in haric or path in paketler or role == "tool":
            continue
        if haric_sid:
            if path not in sid_onbellek:
                try:
                    sid_onbellek[path] = (hkp.meta_oku(path).get("kaynak") or {}).get("sid")
                except Exception:
                    sid_onbellek[path] = None
            if sid_onbellek[path] == haric_sid:
                continue  # ayni oturumun kendi paketi: zaten baglamda
        alt = katla(text)
        isabet = sum(1 for w in kel if w in alt)
        if isabet < gerekli:
            continue
        ozu = " ".join(alt.split())[:300]
        if ozu in metinler:
            continue  # ayni oturumun farkli paketlerindeki ayni ileti: tek kez
        metinler.add(ozu)
        paketler.add(path)  # paket basina tek hatirlatma: cesitlilik
        out.append({"kid": kid, "path": path, "title": title, "i": idx, "role": role,
                    "snippet": " ".join(snip.split())[:180], "skor": round(-skor, 2),
                    "isabet": "%d/%d" % (isabet, len(kel))})
        if len(out) >= limit:
            break
    return out


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
