# -*- coding: utf-8 -*-
"""gorev.py — onaylanmis promt/plan deposu (czip mantigi, .hkp paketi olarak).

Neden: kullanici ham bir gorev yazar, model onu duzgun bir prompta ya da plana
cevirir, kullanici onaylar. O onaylanmis metin oturum gecince kaybolursa ayni
tur-tur konusma bastan yapilir — token'in buyuk kismi ISIN KENDISINE degil, isi
yeniden tarif etmeye gider. Burada onaylanan metin kaynak istekle birlikte kucuk
bir .hkp paketine yazilir; paketler zaten `czip index`/`czip asearch` tarafindan
taraniyor, yani gorev deposu ayrica aranabilir uzun donem hafizaya dusuyor.

Tasarim notlari
  - Ayni dizin, ayni format: gorev paketi de bir .hkp. Ayri bir depo formati
    aciklamak, iki tane yarim hafiza demek olurdu.
  - Mod 'eksiksiz': onaylanmis metin kirpilamaz. Bir prompt'un ortasini atmak
    onu sessizce baska bir prompt yapar.
  - Yan kayit (gorev.json) yalniz listeleme icindir; kaynak gercek pakettir.
    Silinirse `tara()` paketlerden yeniden kurar.
"""
from __future__ import annotations

import json
import os
import re
import time

import hkp

KAYIT_YOL = os.path.join(hkp.PAKET_DIZIN, "gorev.json")
TIPLER = ("promt", "plan")
# Paket basligindaki tip etiketi: "[PROMT] ..." / "[PLAN] ...". Hem insan hem
# `czip listele` ciktisinda gorev paketlerini oturum paketlerinden ayirir.
ETIKET_RE = re.compile(r"^\[(PROMT|PLAN)\]\s*", re.I)


def _kayit_oku():
    try:
        with open(os.path.expanduser(KAYIT_YOL), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _kayit_yaz(kayit):
    yol = os.path.expanduser(KAYIT_YOL)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, indent=1)
    os.replace(gecici, yol)


def _slug(s, n=48):
    return re.sub(r"[^A-Za-z0-9-]+", "-", (s or "")[:n]).strip("-") or "gorev"


def kaydet(tip, baslik, metin, ham=None, etiketler=None):
    """Onaylanmis promt/plan -> .hkp paketi. kid + yol doner."""
    tip = (tip or "").strip().lower()
    if tip not in TIPLER:
        raise ValueError("tip 'promt' veya 'plan' olmali (verilen: %r)" % tip)
    metin = (metin or "").strip()
    if not metin:
        raise ValueError("bos govde kaydedilmez")
    baslik = (baslik or metin.split("\n", 1)[0])[:120].strip()
    d = os.path.expanduser(hkp.PAKET_DIZIN)
    os.makedirs(d, exist_ok=True)
    ad = "gorev-%s-%s-%s.hkp" % (tip, _slug(baslik), time.strftime("%Y%m%d-%H%M%S"))
    yol = os.path.join(d, ad)
    mesajlar = []
    if ham and ham.strip():
        mesajlar.append({"role": "user", "content": ham.strip()})
    mesajlar.append({"role": "assistant", "content": metin})
    tam_baslik = "[%s] %s" % (tip.upper(), baslik)
    sonuc = hkp.sikistir(mesajlar, yol, mod="eksiksiz", baslik=tam_baslik)
    if not sonuc.get("ok"):
        raise RuntimeError(sonuc.get("hata") or "paketlenemedi")
    kid = hkp.id_ata(sonuc["yol"], tam_baslik)
    kayit = _kayit_oku()
    kayit.insert(0, {"kid": kid, "tip": tip, "baslik": baslik,
                     "yol": sonuc["yol"], "t": time.strftime("%Y-%m-%d %H:%M"),
                     "etiket": list(etiketler or []),
                     "ham": (ham or "").strip()[:200],
                     "bayt": sonuc["paket_bayt"], "karakter": len(metin)})
    _kayit_yaz(kayit)
    return {"kid": kid, "tip": tip, "baslik": baslik, "yol": sonuc["yol"],
            "paket_bayt": sonuc["paket_bayt"], "karakter": len(metin)}


def tara():
    """Yan kayit kaybolduysa paket dizininden yeniden kur."""
    d = os.path.expanduser(hkp.PAKET_DIZIN)
    if not os.path.isdir(d):
        return []
    kayit_id = {os.path.abspath(str(v.get("yol"))): k
                for k, v in hkp._kayit_oku().items() if isinstance(v, dict)}
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if not (f.startswith("gorev-") and f.endswith(".hkp")):
            continue
        p = os.path.join(d, f)
        try:
            meta, kayitlar = hkp.yukle(p)
        except Exception:
            continue
        b = str(meta.get("baslik") or f)
        m = ETIKET_RE.match(b)
        out.append({"kid": kayit_id.get(os.path.abspath(p)),
                    "tip": (m.group(1).lower() if m else "?"),
                    "baslik": ETIKET_RE.sub("", b),
                    "yol": p,
                    "t": time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(os.path.getmtime(p))),
                    "bayt": os.path.getsize(p)})
    return out


def listele(tip=None, n=20, sorgu=None):
    kayit = _kayit_oku() or tara()
    if tip:
        kayit = [k for k in kayit if k.get("tip") == tip]
    if sorgu:
        q = sorgu.casefold()
        kayit = [k for k in kayit
                 if q in (k.get("baslik", "") + " " + k.get("ham", "")).casefold()]
    return kayit[:max(1, n)]


def oku(kimlik):
    """kid | dosya yolu | 'son' -> {'tip','baslik','ham','metin'}"""
    yol = None
    if kimlik and kimlik != "son":
        for k in (_kayit_oku() or tara()):
            if k.get("kid") == kimlik or os.path.basename(str(k.get("yol"))) == kimlik:
                yol = k["yol"]
                break
        if yol is None:
            yol = hkp.id_coz(kimlik)
    else:
        aday = (_kayit_oku() or tara())
        if not aday:
            raise ValueError("kayitli gorev yok")
        yol = aday[0]["yol"]
    if not yol or not os.path.exists(os.path.expanduser(yol)):
        raise ValueError("gorev bulunamadi: %s" % kimlik)
    meta, kayitlar = hkp.yukle(yol)
    sozluk = meta.get("soz", [])
    b = str(meta.get("baslik") or "")
    m = ETIKET_RE.match(b)
    ham, metin = "", ""
    for k in kayitlar:
        c = hkp.coz_sozluk(str(k.get("c") or ""), sozluk)
        if k.get("r") == "user":
            ham = c
        elif k.get("r") == "assistant":
            metin = c
    return {"tip": (m.group(1).lower() if m else "?"), "baslik": ETIKET_RE.sub("", b),
            "ham": ham, "metin": metin, "yol": yol}
