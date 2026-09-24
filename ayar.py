# -*- coding: utf-8 -*-
"""ayar.py — czip settings shared by the CLI, the plugin and the context sentinel.

One JSON file so a threshold change takes effect everywhere without editing env
vars or restarting anything. Env still wins when set, so existing setups keep
their behaviour.

  esik_oran   : offer czip when usage / context_length passes this (0.50 = %50)
                — a cap; kalan_token or mutlak_token usually fires first
  kalan_token : fire when the remaining room drops below this many tokens.
                Measured 2026-09-20: a single heavy turn cost 35k tokens, so a
                pure ratio mis-serves a model pool spanning 262k..1.05M windows
                (%90 of 262k leaves 26k = under one heavy turn). 0 disables it.
  mutlak_token: fire when measured prompt tokens reach this absolute count,
                whichever of the three comes first. 0 disables it.
  kademeler   : escalating tiers; each fires once per session
  oto         : true  -> do not ask, decide and pack automatically
                false -> write the offer, let the user press the button
  oto_pasif   : in oto mode, also deactivate merged source sessions
  karar_motoru: decision gate engine — 'laya' (local, default) | 'jev' (cloud)
  bulut_yedegi: if Laya fails, fall back to cloud Jev. OFF by default (KVKK:
                session samples must not leave the machine).
  laya_python : python of the laya-mlx venv (env CZIP_LAYA_PY wins)
"""
from __future__ import annotations

import json
import os

AYAR_YOLU = os.path.expanduser(
    os.environ.get("CZIP_AYAR", "~/.hermes/czip/ayar.json"))

VARSAYILAN = {
    # Bulut yedegi KAPALI. Laya (yerel) cokerse karar kapisi ATLANIR; oturum
    # icerigi disari cikmaz. Acilirsa Laya hatasinda TypeSafe/Jev bulut API'sine
    # oturum basligi + son kullanici mesajlari + arac ciktisi ornekleri gider.
    "bulut_yedegi": False,
    "karar_motoru": "laya",
    "esik_oran": 0.50,
    "kalan_token": 70000,
    "mutlak_token": 0,
    "kademeler": [0.50, 0.75, 0.90],
    "oto": False,
    "oto_pasif": False,
    "jev": True,
}


def _int_env(a, anahtar, env_adi):
    env = os.environ.get(env_adi)
    if env:
        try:
            a[anahtar] = max(0, int(float(env)))
        except ValueError:
            pass
    try:
        a[anahtar] = max(0, int(a.get(anahtar) or 0))
    except Exception:
        a[anahtar] = VARSAYILAN[anahtar]


def oku():
    a = dict(VARSAYILAN)
    try:
        with open(AYAR_YOLU, encoding="utf-8") as f:
            a.update(json.load(f) or {})
    except Exception:
        pass
    # Env overrides stay authoritative: an operator setting CZIP_ESIK_ORAN in a
    # unit file must not be silently overruled by a stale settings file.
    env = os.environ.get("CZIP_ESİK_ORAN") or os.environ.get("CZIP_ESIK_ORAN")
    if env:
        try:
            a["esik_oran"] = float(env)
        except ValueError:
            pass
    _int_env(a, "kalan_token", "CZIP_KALAN_TOKEN")
    _int_env(a, "mutlak_token", "CZIP_MUTLAK_TOKEN")
    if os.environ.get("CZIP_OTO"):
        a["oto"] = os.environ["CZIP_OTO"].strip() not in ("0", "false", "hayir", "no")
    if os.environ.get("CZIP_BULUT_YEDEGI"):
        a["bulut_yedegi"] = os.environ["CZIP_BULUT_YEDEGI"].strip() in ("1", "true", "evet", "yes")
    try:
        a["kademeler"] = sorted({float(x) for x in a.get("kademeler") or []})
    except Exception:
        a["kademeler"] = list(VARSAYILAN["kademeler"])
    return a


def yaz(**kw):
    a = dict(VARSAYILAN)
    try:
        with open(AYAR_YOLU, encoding="utf-8") as f:
            a.update(json.load(f) or {})
    except Exception:
        pass
    a.update({k: v for k, v in kw.items() if v is not None})
    os.makedirs(os.path.dirname(AYAR_YOLU), exist_ok=True)
    tmp = AYAR_YOLU + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
    os.replace(tmp, AYAR_YOLU)
    return a


def esik_token(ctx, a=None):
    """Bu context penceresi icin gercek tetik esigi (token cinsinden kullanim).

    esik = min(oran * ctx, ctx - kalan_token, mutlak_token) -> hangisi ONCE gelirse.
    Kucuk pencerede kalan_token, buyuk pencerede oran belirleyici olur; mutlak
    token (0 degilse) hepsinin onune gecebilir.
    """
    a = a or oku()
    oran_esigi = float(a.get("esik_oran", 0.50)) * ctx
    kalan = int(a.get("kalan_token") or 0)
    esik = int(min(oran_esigi, ctx - kalan) if kalan else oran_esigi)
    mutlak = int(a.get("mutlak_token") or 0)
    if mutlak > 0:
        esik = min(esik, mutlak)
    return esik


def kademe_bul(oran, kademeler=None):
    """Highest tier the given ratio has passed, or None."""
    ks = sorted(kademeler if kademeler is not None else oku()["kademeler"])
    gecilen = [k for k in ks if oran >= k]
    return gecilen[-1] if gecilen else None
