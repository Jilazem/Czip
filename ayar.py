# -*- coding: utf-8 -*-
"""ayar.py — czip settings shared by the CLI, the plugin and the context sentinel.

One JSON file so a threshold change takes effect everywhere without editing env
vars or restarting anything. Env still wins when set, so existing setups keep
their behaviour.

  esik_oran : offer czip when usage / context_length passes this (0.50 = %50)
              — a cap; kalan_token or mutlak_token usually fires first
  kalan_token: fire when the remaining room drops below this many tokens.
              Measured 2026-09-20: a single heavy turn cost 35k tokens, so a
              pure ratio mis-serves a model pool spanning 262k..1.05M windows
              (%90 of 262k leaves 26k = under one heavy turn). 0 disables it.
  mutlak_token: fire when measured prompt tokens reach this absolute count,
              whichever of the three comes first. 0 disables it. This is the
              per-session auto trigger (64_000 = pack and continue every session
              once it has used 64k tokens).
  kademeler : escalating tiers; each fires once per session
  oto       : true  -> do not ask, decide and pack automatically (Jev-style)
              false -> write the offer, let the user press the button
  oto_pasif : in oto mode, also deactivate merged source sessions
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
    # KVKK: dava verisi buluta cikmaz kurali geregi varsayilan kapali.
    "bulut_yedegi": False,
    "esik_oran": 0.50,
    "kalan_token": 70000,
    "mutlak_token": 0,
    "kademeler": [0.50, 0.75, 0.90],
    "oto": False,
    "oto_pasif": False,
    "jev": True,
}


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
    env = os.environ.get("CZIP_KALAN_TOKEN")
    if env:
        try:
            a["kalan_token"] = max(0, int(float(env)))
        except ValueError:
            pass
    try:
        a["kalan_token"] = max(0, int(a.get("kalan_token") or 0))
    except Exception:
        a["kalan_token"] = VARSAYILAN["kalan_token"]
    env = os.environ.get("CZIP_MUTLAK_TOKEN")
    if env:
        try:
            a["mutlak_token"] = max(0, int(float(env)))
        except ValueError:
            pass
    try:
        a["mutlak_token"] = max(0, int(a.get("mutlak_token") or 0))
    except Exception:
        a["mutlak_token"] = 0
    if os.environ.get("CZIP_OTO"):
        a["oto"] = os.environ["CZIP_OTO"].strip() not in ("0", "false", "hayir", "no")
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
    token (0 degilse) hepsinin onune gecebilir. Boylece tek bir yuzde 262k ile
    1.05M arasini birlikte idare eder, 64k gibi sabit bir tavan da konabilir.
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
