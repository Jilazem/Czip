# -*- coding: utf-8 -*-
"""yon.py — yon karti: paketten AI'ya "nereden devam, neye dikkat" talimati.

Neden: compaction/ozet "ne yapildi"yi tasir, "ne KARARLASTIRILDI ve neden"i
kaybeder (compaction amnesia). Yeni oturum ilk isi iyi yapar, sonrakini bozar.
Yon karti bunu LLM'siz, kaliplarla cikarir ve paket meta'sina yazar:

  hedef      : ilk kullanici istegi (isin tanimi)
  son_istek  : son kullanici istegi (+ yanitsiz mi?)
  kararlar   : "cunku / yerine / olmadi / asla / because / instead ..." satirlari
  acik       : "TODO / kalan / sonraki adim / bekliyor / next step ..." satirlari
  hatalar    : son hata ciktisi (cozulmemis olabilir)
  dosyalar   : arac cagrilarinin dokundugu dosyalar (diskte dogrulamak icin)
  sonraki    : karar mekanizmasinin onerdigi TEK sonraki adim
  dogrula    : devam etmeden once diskle karsilastirma talimati

Her satir ileti numarasi (#i) tasir: AI ayrinti icin `czip aralik <id> i` cagirir.
Karar kapisi acikken (Laya) aday satirlar "hala gecerli mi?" diye elenir;
kapali/ulasilamazsa kalip sonucu aynen kullanilir.
"""
from __future__ import annotations

import json
import os
import re

KARAR_IPUCU = re.compile(
    r"(?i)(\bçünkü\b|\bcunku\b|karar ver|kararlaştır|\bkarar:|\byerine\b|\bolmad[ıi]\b|\bolmuyor\b|\bgeri al"
    r"|\bvazgeç\w*|\bseçtik\b|\btercih\w*|\bkural\w*|\basla\b|\bsakın\b|\bkesinlikle\b"
    r"|\bbecause\b|\bdecided\b|\bdecision\b|\binstead\b|\breverted\b|\brollback\b"
    r"|\bdon't\b|\bdo not\b|\bmust not\b|\bnever\b|\bchose\b|\bgoing with\b)")
ACIK_IPUCU = re.compile(
    r"(?i)(\bTODO\b|\bFIXME\b|yapılacak|yapilacak|sıradaki|siradaki|sonraki adım"
    r"|sonraki adim|\bkalan\b|\beksik\b|bekliyor|beklemede|\bhenüz\b|\bhenuz\b"
    r"|next step|\bnext:|\bremaining\b|\bpending\b|not yet|follow[- ]up|- \[ \])")
HATA_IPUCU = re.compile(
    r"(?i)(traceback|exception|\berror\b|failed|\bhata\b|başarısız|basarisiz|denied"
    r"|no such file|not found|exit code [1-9])")
YOL_ANAHTAR = ("file_path", "path", "notebook_path", "filename", "dosya")
SATIR_MAX = 170


def _satirlar(metin):
    for s in re.split(r"(?<=[.!?])\s+|\n+", metin or ""):
        s = " ".join(s.split()).strip("-*# ")
        if 12 <= len(s):
            yield s[:SATIR_MAX] + ("…" if len(s) > SATIR_MAX else "")


def _ilk_satir(metin, n=SATIR_MAX):
    s = " ".join(str(metin or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _gercek_istek(c):
    """Sistem enjeksiyonu / oturum siniri / arac-sonucu olmayan kullanici metni mi?"""
    c = (c or "").strip()
    return bool(c) and not c.startswith(("[", "──", "<", "{"))


def _dosyalar(kayitlar):
    say = {}
    for k in kayitlar:
        for tc in k.get("tc") or []:
            try:
                a = json.loads(tc.get("a") or "{}")
            except Exception:
                continue
            if not isinstance(a, dict):
                continue
            for ad in YOL_ANAHTAR:
                v = a.get(ad)
                if isinstance(v, str) and 2 < len(v) < 300 and "\n" not in v:
                    say[v] = say.get(v, 0) + 1
    return [y for y, _ in sorted(say.items(), key=lambda x: -x[1])[:8]]


def _laya_ele(adaylar, baslik):
    """Karar kapisi: 'bu satir devam icin hala onemli mi?' — dusuk olanlari atar."""
    if not adaylar:
        return adaylar
    try:
        import karar as _k
        sorular = {"y%d" % j: ("Line from a working session (%s). Is this still an "
                               "important decision or open task for whoever continues "
                               "the work?\nLINE: %s" % (tur, metin))
                   for j, (tur, _, metin) in enumerate(adaylar)}
        cevap, _ = _k.sor("Task: " + str(baslik or "")[:200], sorular, timeout=30)
    except Exception:
        return adaylar  # motor yok -> kalip sonucu aynen
    return [a for j, a in enumerate(adaylar) if cevap.get("y%d" % j, 1.0) >= 0.30]


def kart(kayitlar, baslik=None, karar_kapisi=False):
    """Coz_sozluk UYGULANMAMIS (duz metin) kayitlardan yon karti sozlugu."""
    n = len(kayitlar)
    istekler = [(i, k.get("c")) for i, k in enumerate(kayitlar)
                if k.get("r") == "user" and _gercek_istek(k.get("c"))]
    kararlar, acik, hatalar = [], [], []
    gorulen = set()
    kuyruk_bas = max(0, n - max(60, n * 2 // 5))  # acik isler: son %40 / son 60 ileti
    # TUR SONU yaniti: arkasindan yeni kullanici istegi (ya da son) gelen asistan
    # metni. Ara anlatim ("simdi X'e bakiyorum") arac cagrisina gider; karar ve
    # kalan is ozetleri tur sonunda yazilir — sinyal orada, gurultu arada.
    tur_sonu = set()
    for i, k in enumerate(kayitlar):
        if k.get("r") == "assistant" and k.get("c"):
            sonraki_k = kayitlar[i + 1] if i + 1 < n else None
            if sonraki_k is None or (sonraki_k.get("r") == "user"
                                     and _gercek_istek(sonraki_k.get("c"))):
                tur_sonu.add(i)
    for i, k in enumerate(kayitlar):
        r, c = k.get("r"), k.get("c")
        if not isinstance(c, str) or not c:
            continue
        if r == "tool":
            if i >= n - 15 and HATA_IPUCU.search(c[:3000]):
                hatalar.append((i, _ilk_satir(c, 140)))
            continue
        if r == "user":
            # istekler hedef/son_istek olarak ayrica gorunur; yalniz KURAL koyan
            # kisa kullanici satirlari ("asla X yapma") karar sayilir
            if not _gercek_istek(c) or i in (istekler[0][0], istekler[-1][0]):
                continue
        elif i not in tur_sonu:
            continue
        for s in _satirlar(c[:20000]):
            anahtar = s.lower()[:80]
            if anahtar in gorulen:
                continue
            if KARAR_IPUCU.search(s):
                kararlar.append((i, s))
                gorulen.add(anahtar)
            elif i >= kuyruk_bas and ACIK_IPUCU.search(s):
                acik.append((i, s))
                gorulen.add(anahtar)
    kararlar, acik = kararlar[-6:], acik[-5:]
    if karar_kapisi:
        adaylar = [("decision", i, s) for i, s in kararlar] + [("open task", i, s) for i, s in acik]
        kalan = _laya_ele(adaylar, baslik)
        kararlar = [(i, s) for t, i, s in kalan if t == "decision"]
        acik = [(i, s) for t, i, s in kalan if t == "open task"]
    son = kayitlar[-1] if kayitlar else {}
    yanitsiz = son.get("r") == "user"
    son_final = max(tur_sonu) if tur_sonu else -1
    yarim = bool(istekler) and not yanitsiz and istekler[-1][0] > son_final
    # Karar mekanizmasi: TEK net sonraki adim (AI'ya yon)
    if yanitsiz and istekler:
        sonraki = "Kullanicinin son istegi yanitsiz: #%d — once onu yanitla." % istekler[-1][0]
    elif yarim:
        sonraki = ("Son istek (#%d) uzerinde calisiliyordu, tur bitmeden kesildi — "
                   "#%d sonrasini oku, kaldigin adimdan devam et." % (istekler[-1][0], istekler[-1][0]))
    elif hatalar and hatalar[-1][0] >= n - 4:
        sonraki = "Son adim hatayla bitti: #%d — once hatayi coz." % hatalar[-1][0]
    elif acik:
        sonraki = "Acik is: #%d %s" % acik[-1]
    elif istekler:
        sonraki = "Son istegin (#%d) tamamlandigini dogrula, sonra kullaniciya sor." % istekler[-1][0]
    else:
        sonraki = ""
    return {
        "hedef": (istekler[0][0], _ilk_satir(istekler[0][1], 200)) if istekler else None,
        "son_istek": (istekler[-1][0], _ilk_satir(istekler[-1][1], 200)) if istekler else None,
        "yanitsiz": yanitsiz,
        "yarim": yarim,
        "kararlar": kararlar,
        "acik": acik,
        "hatalar": hatalar[-2:],
        "dosyalar": _dosyalar(kayitlar),
        "sonraki": sonraki,
        "toplam": n,
    }


def metin(k, kid=None, baslik=None):
    """Yon kartini AI'ya verilecek kisa metne cevirir (~200-500 token)."""
    if not k:
        return ""
    ref = kid or "<id>"
    s = ["YON KARTI" + (" — %s" % baslik if baslik else "") + (" [%s]" % kid if kid else "")]
    if k.get("hedef"):
        s.append("hedef: #%d %s" % tuple(k["hedef"]))
    if k.get("son_istek") and k.get("son_istek") != k.get("hedef"):
        s.append("son istek: #%d %s" % tuple(k["son_istek"]))
    if k.get("kararlar"):
        s.append("kararlar (bozma):")
        s.extend("  #%d %s" % tuple(x) for x in k["kararlar"])
    if k.get("acik"):
        s.append("acik isler:")
        s.extend("  #%d %s" % tuple(x) for x in k["acik"])
    if k.get("hatalar"):
        s.append("son hata: " + "; ".join("#%d %s" % tuple(x) for x in k["hatalar"]))
    if k.get("dosyalar"):
        s.append("dosyalar: " + ", ".join(os.path.basename(y.rstrip("/")) or y
                                          for y in k["dosyalar"]))
    if k.get("sonraki"):
        s.append("SONRAKI ADIM: " + k["sonraki"])
    s.append("DOGRULA: devam etmeden once listelenen dosyalari/branch'i diskte kontrol et "
             "(ls, git status); paketle celisirse dur ve sor. Ayrinti: czip aralik %s <i>" % ref)
    return "\n".join(s)
