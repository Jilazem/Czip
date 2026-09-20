# -*- coding: utf-8 -*-
"""ayar.py — czip settings shared by the CLI, the plugin and the context sentinel.

One JSON file so a threshold change takes effect everywhere without editing env
vars or restarting anything. Env still wins when set, so existing setups keep
their behaviour.

  esik_oran : offer czip when usage / context_length passes this (0.50 = %50)
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
    "esik_oran": 0.50,
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


def kademe_bul(oran, kademeler=None):
    """Highest tier the given ratio has passed, or None."""
    ks = sorted(kademeler if kademeler is not None else oku()["kademeler"])
    gecilen = [k for k in ks if oran >= k]
    return gecilen[-1] if gecilen else None
