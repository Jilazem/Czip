# -*- coding: utf-8 -*-
"""hafiza.py — czip'in "ne yaptim?" hafizasi: gunluk + RAG + acilis brifingi.

Uc katman, ucuzdan pahaliya:
  1) GUNLUK  (gunluk.jsonl) — her paket tek satir: tarih, proje, id, baslik,
     yon karti ozeti. Acilista okunur; LZMA acmaz, milisaniyedir.
  2) DEPO    (depo.py FTS5) — tum paketlerin tam metni; `hatirla` alaka
     kapisiyla ilgili gecmis iletiyi bulur.
  3) PAKET   — kaynak gercek; `czip aralik <id> <i>` ile birebir metin.

`kaydet()` her paketlemeden sonra cagrilir (CLI, MCP, hook): gunluge yazar ve
yeni paketi indekse ekler. Hata firlatmaz — hafiza yan etkidir, paketi bozmaz.
"""
from __future__ import annotations

import json
import os
import time

import hkp

GUNLUK_MAX = 2000  # satir; temizlik fazlasini budar


def gunluk_yolu():
    return os.path.join(os.path.dirname(os.path.expanduser(hkp.KAYIT_YOL)), "gunluk.jsonl")


def _proje(kaynak, baslik):
    k = kaynak or {}
    return k.get("proje") or (os.path.basename(k["cwd"].rstrip("/")) if k.get("cwd") else "") \
        or (baslik or "")[:40]


def kaydet(r, kid, kaynak=None):
    """Paket sonrasi: gunluk satiri + indeks guncellemesi. Doner: kisa durum sozlugu."""
    durum = {}
    try:
        yon = r.get("yon") or {}
        satir = {
            "t": time.strftime("%Y-%m-%d %H:%M"),
            "kid": kid,
            "yol": r.get("yol"),
            "baslik": (r.get("baslik") or "")[:100],
            "proje": _proje(kaynak or r.get("kaynak"), r.get("baslik")),
            "cwd": (kaynak or r.get("kaynak") or {}).get("cwd"),
            "sid": (kaynak or r.get("kaynak") or {}).get("sid"),
            "ileti": r.get("mesaj"),
            "hedef": (yon.get("hedef") or [None, ""])[1],
            "sonraki": yon.get("sonraki") or "",
            "acik": [s for _, s in (yon.get("acik") or [])][-3:],
            "kararlar": [s for _, s in (yon.get("kararlar") or [])][-3:],
        }
        y = gunluk_yolu()
        os.makedirs(os.path.dirname(y), exist_ok=True)
        with open(y, "a", encoding="utf-8") as f:
            f.write(json.dumps(satir, ensure_ascii=False) + "\n")
        durum["gunluk"] = True
    except Exception as e:  # noqa: BLE001
        durum["gunluk_hata"] = str(e)[:80]
    try:
        import depo
        g = depo.guncelle(butce_sn=20)
        durum["indeks"] = g["yeni"] + g["guncellenen"]
    except Exception as e:  # noqa: BLE001
        durum["indeks_hata"] = str(e)[:80]
    return durum


def gunluk(proje=None, cwd=None, n=5):
    """Son gunluk satirlari (en yeni sonda). proje/cwd verilirse onunla sinirli."""
    y = gunluk_yolu()
    if not os.path.exists(y):
        return []
    out = []
    with open(y, encoding="utf-8", errors="replace") as f:
        for s in f:
            try:
                o = json.loads(s)
            except ValueError:
                continue
            if cwd and os.path.normpath(o.get("cwd") or "") != os.path.normpath(cwd):
                continue  # cwd'siz (Hermes) kayit baska projenin brifingine sizmasin
            if proje and not cwd and o.get("proje") != proje:
                continue
            if o.get("yol") and not os.path.exists(o["yol"]):
                # temizlik tasidiysa id yonlendirilmis olabilir
                yeni = hkp.id_coz(o.get("kid")) if o.get("kid") else None
                if not (yeni and os.path.exists(yeni)):
                    continue
            out.append(o)
    # ayni oturumun ardisik paketlerinden yalniz en yenisi
    tekil, gorulen = [], set()
    for o in reversed(out):
        anahtar = o.get("sid") or o.get("kid")
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        tekil.append(o)
        if len(tekil) >= n:
            break
    return list(reversed(tekil))


def brifing(cwd=None, proje=None, n=3, yon_dahil=True):
    """Acilis brifingi: bu projede son yapilanlar + en son paketin yon karti.

    Hedef boyut ~300-700 token. Hic kayit yoksa bos string (sessiz)."""
    kayitlar = gunluk(proje=proje, cwd=cwd, n=n)
    if not kayitlar:
        return ""
    s = ["[CZIP HAFIZA] Bu projede son isler (eskiden yeniye):"]
    for o in kayitlar:
        s.append("- %s %s \"%s\" (%s ileti)%s" % (
            o["t"][5:], o.get("kid") or "?", (o.get("baslik") or "")[:60], o.get("ileti") or "?",
            (" — sonraki: " + o["sonraki"][:110]) if o.get("sonraki") else ""))
    son = kayitlar[-1]
    if yon_dahil and son.get("kid"):
        try:
            import yon
            yol = hkp.id_coz(son["kid"])
            meta = hkp.meta_oku(yol)
            if meta.get("yon"):
                s.append(yon.metin(meta["yon"], son["kid"], (meta.get("baslik") or "")[:60]))
        except Exception:  # noqa: BLE001
            pass
    s.append("ARAC: czip harita <id> (ucuz harita) · czip ara <id> \"...\" · "
             "czip arsivara \"...\" (tum gecmis) · czip aralik <id> <i>")
    return "\n".join(s)


def hatirlatma(metin, haric=(), limit=3, atla=(), haric_sid=None):
    """Kullanici istegine ilgili gecmis — alaka kapisindan gecenler, kisa metin.

    atla: bu oturumda zaten hatirlatilmis "yol#i" anahtarlari (tekrar yok)."""
    try:
        import depo
        isabet = [h for h in depo.hatirla(metin, limit=limit + len(atla), haric=haric,
                                    haric_sid=haric_sid)
                  if "%s#%s" % (h["path"], h["i"]) not in set(atla)][:limit]
    except Exception:  # noqa: BLE001
        return "", []
    if not isabet:
        return "", []
    s = ["[CZIP HATIRLATMA] Bu istekle ilgili gecmis is (tam metin: czip aralik <id> <i>):"]
    for h in isabet:
        s.append("- %s #%s %s: %s" % (h["kid"] or os.path.basename(h["path"])[:24], h["i"],
                                      (h["title"] or "")[:40], h["snippet"]))
    return "\n".join(s), isabet
