#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""laya_kapi — czip karar kapisi icin kalici Laya isci sureci (yerel, anahtarsiz).

laya-mlx kendi venv'inde calisir; bu betik o venv ile baslatilir ve stdin'den
satir satir JSON alir: {"state": "...", "questions": {"q1": "talimat", ...}}
stdout'a tek satir JSON yazar: {"answers": {"q1": 0.87, ...}} veya {"error": "..."}
Model bir kez yuklenir (842MB); sonraki sorular ayni surecte yanitlanir.

czip tarafi bu sureci karar.py uzerinden baslatir (CZIP_LAYA_PY = laya-mlx
venv'inin python'u). Veri makineden cikmaz; Jev'in yerini bu surec aldi.
"""
import json
import os
import re
import sys
import time
import urllib.request

MODEL = os.environ.get("CZIP_LAYA_MODEL", "aac6fef/laya-mlx")
# Laya Turkcede zayif — TR icerik once yerel node1 (Qwen) ile EN'e cevrilir
# (Hermes laya-tr koprusunun ayni akisi). CZIP_LAYA_CEVIRI=0 ile kapatilir.
NODE1_URL = os.environ.get("LAYA_NODE1_URL", "http://127.0.0.1:8888/v1")
# Model ADI SABITLENMEZ. Yerel uctaki model degisince (surum yukseltme, baska
# bir model yuklenmesi) sabit ad 404 ile doner ve kopru sessizce devre disi
# kalirdi. Dogrusu: uca "su an ne yuklu?" diye sormak. Env ile zorlanabilir.
NODE1_MODEL = os.environ.get("LAYA_NODE1_MODEL", "")  # bos = uca sor
_MODEL_ONBELLEK = {"ad": "", "ts": 0.0}
MODEL_ONBELLEK_SN = 300.0
CEVIRI_ACIK = os.environ.get("CZIP_LAYA_CEVIRI", "1") != "0"
CEVIRI_ZAMAN_ASIMI = float(os.environ.get("CZIP_LAYA_CEVIRI_SN", "90"))
CEVIRI_AZAMI = 1200  # parca basina cevrilecek azami karakter
TR_HARF = re.compile(r"[ğĞışŞİçÇöÖüÜ]")
TR_KELIME = re.compile(r"(?i)\b(ve|ile|için|bir|bu|olarak|değer|dosya|rapor|parsel"
                       r"|mahalle|sokak|tarih|hata|kayıt|sayfa)\b")
_agent = None


def _aktif_model() -> str:
    """Yerel uctaki aktif modeli dondurur (5 dk onbellekli).

    Sira: LAYA_NODE1_MODEL env -> /v1/models ilk kayit -> "" (cagri atlanir).
    Ad uydurulmaz; uc cevap vermezse ceviri yapilmaz ve metin oldugu gibi kalir.
    """
    if NODE1_MODEL:
        return NODE1_MODEL
    simdi = time.time()
    if _MODEL_ONBELLEK["ad"] and simdi - _MODEL_ONBELLEK["ts"] < MODEL_ONBELLEK_SN:
        return _MODEL_ONBELLEK["ad"]
    try:
        with urllib.request.urlopen(NODE1_URL + "/models", timeout=5) as y:
            kayitlar = (json.loads(y.read()) or {}).get("data") or []
        ad = str((kayitlar[0] or {}).get("id") or "") if kayitlar else ""
    except Exception:  # noqa: BLE001
        ad = ""
    if ad:
        _MODEL_ONBELLEK.update(ad=ad, ts=simdi)
    return ad


def _turkce_mi(t: str) -> bool:
    ornek = t[:1500]
    return bool(TR_HARF.search(ornek)) or len(TR_KELIME.findall(ornek)) >= 3


def _bozuk_ceviri(kaynak: str, cevrilen: str) -> bool:
    """Ceviri icerigi yutmus mu?

    Olcum (2026-09-24, 4 gercek TR arac ciktisi): toplu JSON istendiginde model
    her alan icin "..." dondurdu. Eski kod bunu kabul ediyordu, 1705 karakterlik
    ornek uc noktaya iniyor ve Laya hepsine 0.1474 verip SILME esiginin altina
    atiyordu — gercek iceriginde 0.73 (kesin tut) alan ornek dahil. Kisacasi
    sessiz veri kaybi. Artik kirpilmis ceviri reddedilir, orijinal metinle devam.
    """
    c = cevrilen.strip()
    if not c or re.fullmatch(r"[.…\s]*", c):
        return True
    return len(c) < max(40, len(kaynak.strip()) * 0.25)


def _cevir(parcalar: dict) -> dict:
    """{anahtar: TR metin} -> {anahtar: EN metin}. Hata/bozukluk olursa orijinal doner.

    Parcalar TEK TEK sorulur: ham arac ciktilari cogu zaman kendileri de JSON;
    bir JSON talimatinin icine gomunce model ic JSON'u ayristirip kendi
    alanlarini dondurmeye ya da hepsini "..." ile ozetlemeye basliyordu.
    """
    if not (CEVIRI_ACIK and parcalar):
        return parcalar
    model = _aktif_model()
    if not model:
        return parcalar  # uc yok/cevapsiz -> ceviri yok, metin korunur
    cikti = dict(parcalar)
    for k, ham in parcalar.items():
        kaynak = ham[:CEVIRI_AZAMI]
        istem = ("Translate the following text from Turkish to English. "
                 "Keep code, identifiers, paths, numbers and JSON structure exactly as they are. "
                 "Translate only human-language words. Do not summarize, do not elide, "
                 "do not add commentary. Output the translated text only.\n\n"
                 "<<<TEXT\n" + kaynak + "\nTEXT>>>")
        # Dusunme KAPALI. Olcum (2026-09-24, ayni ornek):
        #   dusunme acik          -> 67,5s, content BOS, reasoning 9417 kar
        #   enable_thinking=false ->  8,3s, content 1310 kar (temiz ceviri)
        govde = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": istem}],
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0, "max_tokens": 3000}).encode("utf-8")
        try:
            req = urllib.request.Request(NODE1_URL + "/chat/completions", data=govde,
                                         headers={"Content-Type": "application/json"})
            msg = json.loads(urllib.request.urlopen(
                req, timeout=CEVIRI_ZAMAN_ASIMI).read())["choices"][0]["message"]
            # reasoning'e DUSULMEZ: o ceviri degil, modelin kendi notudur.
            metin = (msg.get("content") or "").strip()
            if metin.startswith("<<<TEXT"):
                metin = metin[len("<<<TEXT"):].strip()
            if metin.endswith("TEXT>>>"):
                metin = metin[:-len("TEXT>>>")].strip()
            if metin and not _bozuk_ceviri(kaynak, metin):
                cikti[k] = metin
        except Exception:  # noqa: BLE001 (ceviri yoksa TR metinle devam)
            pass
    return cikti


def _ajan():
    global _agent
    if _agent is None:
        import laya_mlx as laya  # yalniz laya-mlx venv'inde bulunur
        _agent = laya.load(MODEL)
    return _agent


def isle(istek: dict) -> dict:
    """Tek istek -> tek cevap sozlugu (test edilebilir cekirdek)."""
    durum = istek.get("state") or ""
    ham = {"__state__": json.dumps(durum, ensure_ascii=False)
           if isinstance(durum, dict) else str(durum)}
    for q, t in (istek.get("questions") or {}).items():
        ham[q] = str(t.get("instructions", "")) if isinstance(t, dict) else str(t)
    tr = {k: v for k, v in ham.items() if _turkce_mi(v)}
    en = _cevir(tr) if tr else {}
    metin = {k: en.get(k, v) for k, v in ham.items()}
    state = {"context": metin.pop("__state__")}
    if os.environ.get("CZIP_LAYA_DEBUG"):
        sys.stderr.write("CEVIRI> " + json.dumps({"state": state, "q": metin},
                                                 ensure_ascii=False)[:1200] + "\n")
    # deger str ise noul sorusu; dict ise (choice/score/noul + criteria) aynen gecer
    sorular = {}
    for q, t in metin.items():
        asil = (istek.get("questions") or {}).get(q)
        if isinstance(asil, dict):
            sorular[q] = dict(asil, instructions=t)
        else:
            sorular[q] = {"type": "noul", "instructions": t}
    r = _ajan().predict(state, sorular)
    cikti, guven = {}, {}
    for q, a in (r.get("answers") or {}).items():
        if isinstance(a, dict) and a.get("noul") is not None:
            cikti[q] = float(a["noul"])
            if a.get("confidence") is not None:
                guven[q] = float(a["confidence"])
    if not (cikti or r.get("answers")):
        return {"error": "bos_answers"}
    return {"answers": cikti, "confidence": guven, "raw": r.get("answers") or {}}


def main() -> int:
    for satir in sys.stdin:
        satir = satir.strip()
        if not satir:
            continue
        try:
            cevap = isle(json.loads(satir))
        except Exception as e:  # noqa: BLE001 (isci surec: hata satiri dondurur, olmez)
            cevap = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        sys.stdout.write(json.dumps(cevap, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
