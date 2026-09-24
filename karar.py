# -*- coding: utf-8 -*-
"""karar.py — czip karar kapisinin motor secicisi (Laya yerel / Jev bulut).

Tarihce: karar kapisi once Jev'di (typesafe.ai System-One, bulut). 21.09.2026'da
gizlilik gerekcesiyle kapatildi — Jev'e oturum basligi, son kullanici mesajlari
ve arac ciktisi ornekleri gidiyordu. 23.09.2026'dan beri varsayilan motor YEREL
Laya (laya-mlx): model makinede calisir, veri disari cikmaz, anahtar gerekmez.

Tek arayuz:  sor(durum, sorular) -> {qid: noul (0..1)}
  - motor = CZIP_KARAR_MOTORU env > ayar.json 'karar_motoru' > 'laya'
  - Laya calismazsa ve ayar 'bulut_yedegi' ACIKSA + Jev anahtari varsa Jev'e duser;
    yoksa ValueError -> cagiran taraf statik davranisa doner (sessiz fallback).
  - motor='jev' acikca secilirse eski bulut yolu kullanilir (anahtar ister).

Laya isci sureci (laya_kapi.py) bir kez baslatilir, model bir kez yuklenir;
sonraki sorular ayni surece satir satir JSON olarak gider.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

KOK = os.path.dirname(os.path.abspath(__file__))

# Motor basina esikler. Laya puani baglama duyarli oldugu icin silme esigi
# Jev'inkinden korumaci (0.15 < 0.30): supheli cikti paketten ATILMAZ.
ESIKLER = {
    "laya": {"sil": 0.15, "tut": 0.55, "birlestir": 0.60},
    "jev": {"sil": 0.30, "tut": 0.55, "birlestir": 0.60},
}
LAYA_VARSAYILAN_PY = "~/007-HERMES/04-ARASTIRMA/laya-mlx/.venv/bin/python"
LAYA_ZAMAN_ASIMI = float(os.environ.get("CZIP_LAYA_SN", "180"))


def _ayar():
    try:
        import ayar as _a
        return _a.oku()
    except Exception:
        return {}


def motor_adi():
    """Secili birincil motor: 'laya' | 'jev'."""
    m = (os.environ.get("CZIP_KARAR_MOTORU") or _ayar().get("karar_motoru")
         or "laya").strip().lower()
    return m if m in ESIKLER else "laya"


def esikler(motor=None):
    return dict(ESIKLER[motor or motor_adi()])


# ------------------------------------------------------------------ Laya
class _LayaIsci:
    """laya_kapi.py'yi laya-mlx venv python'u ile tek sefer baslatir."""

    def __init__(self):
        self.p = None
        self.kilit = threading.Lock()

    def _python(self):
        py = os.path.expanduser(os.environ.get("CZIP_LAYA_PY")
                                or _ayar().get("laya_python") or LAYA_VARSAYILAN_PY)
        if not os.path.isfile(py):
            raise ValueError("laya_yok (CZIP_LAYA_PY: %s)" % py)
        return py

    def _baslat(self):
        kapi = os.environ.get("CZIP_LAYA_KAPI") or os.path.join(KOK, "laya_kapi.py")
        self.p = subprocess.Popen([self._python(), kapi], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", bufsize=1)

    def sor(self, durum, sorular, timeout=LAYA_ZAMAN_ASIMI):
        with self.kilit:
            if self.p is None or self.p.poll() is not None:
                self._baslat()
            satir = json.dumps({"state": durum, "questions": sorular}, ensure_ascii=False)
            sonuc = {}

            def _oku():
                try:
                    self.p.stdin.write(satir + "\n")
                    self.p.stdin.flush()
                    sonuc["s"] = self.p.stdout.readline()
                except Exception as e:  # noqa: BLE001
                    sonuc["e"] = str(e)

            t = threading.Thread(target=_oku, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                self.kapat()
                raise ValueError("laya_zaman_asimi")
            if "e" in sonuc or not sonuc.get("s"):
                self.kapat()
                raise ValueError("laya_surec_" + str(sonuc.get("e", "bos_cevap"))[:60])
            try:
                veri = json.loads(sonuc["s"])
            except ValueError:
                raise ValueError("laya_bozuk_cevap")
            if veri.get("error"):
                raise ValueError("laya:" + str(veri["error"])[:80])
            out = {q: float(v) for q, v in (veri.get("answers") or {}).items()}
            if not out:
                raise ValueError("bos_answers")
            return out

    def kapat(self):
        if self.p is not None:
            try:
                self.p.kill()
                self.p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            for akis in (self.p.stdin, self.p.stdout):
                try:
                    akis.close()
                except Exception:  # noqa: BLE001
                    pass
            self.p = None


_LAYA = _LayaIsci()


def _laya(durum, sorular, timeout):
    return _LAYA.sor(durum, sorular, timeout=max(timeout, LAYA_ZAMAN_ASIMI))


def _jev(durum, sorular, timeout):
    import hkp
    key = hkp._jev_anahtar()
    if not key:
        raise ValueError("anahtar_yok")
    return hkp._jev_batch(key, durum, sorular, timeout)


def sor(durum, sorular, timeout=25):
    """{qid: talimat} -> ({qid: noul}, kullanilan_motor). Basarisizsa ValueError.

    Bulut yedegi varsayilan KAPALI (KVKK): Laya cokerse karar kapisi atlanir,
    oturum icerigi disari cikmaz."""
    motor = motor_adi()
    if motor == "jev":
        return _jev(durum, sorular, timeout), "jev"
    try:
        return _laya(durum, sorular, timeout), "laya"
    except ValueError as e:
        if _ayar().get("bulut_yedegi"):
            try:
                return _jev(durum, sorular, timeout), "jev"
            except ValueError as e2:
                raise ValueError("%s; jev: %s" % (e, e2))
        raise
