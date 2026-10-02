# -*- coding: utf-8 -*-
"""ccd_dokum.py — Claude Code oturum disa aktarimi (transcript.jsonl) -> czip iletleri.

Neden ayri: Hermes oturumlari state.db'de durur, Claude Code oturumlari durmaz.
Ikisi ayni ise ait olabilir (ayni proje, ayni gun, biri otekinin devami), ama
`czip birlestir` state.db'ye baktigi icin Claude Code tarafini goremiyordu.
Bu koprü dokumu cevirir; birlestirme kurallari (sinir isaretleri, zaman sirasi,
tekrar ayiklama) birlestir.kaynaklari_birlestir() ile AYNI kalir.

Blok duzlestirme: assistant iletisindeki 'text' bloklari govde olur, 'tool_use'
bloklari tool_calls'a, user iletisindeki 'tool_result' bloklari ayri 'tool'
iletine dusurulur — czip'in kayit_yap() bekledigi sekil budur.
"""
from __future__ import annotations

import json
import os


def _zaman(o):
    t = o.get("timestamp")
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str) and t:
        try:
            import datetime as _d
            return _d.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _metin(bloklar):
    if isinstance(bloklar, str):
        return bloklar
    parca = []
    for b in bloklar or []:
        if not isinstance(b, dict):
            parca.append(str(b))
        elif b.get("type") == "text" and b.get("text"):
            parca.append(str(b["text"]))
        elif b.get("type") == "thinking" and b.get("thinking"):
            parca.append("[dusunce] " + str(b["thinking"]))
    return "\n".join(parca)


def oku(yol):
    """transcript.jsonl -> (mesajlar, baslik). Bozuk satirlar sessizce atlanir."""
    mesajlar, baslik = [], ""
    with open(os.path.expanduser(yol), encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                o = json.loads(satir)
            except Exception:
                continue
            tip = o.get("type")
            if tip == "custom-title":
                # Disa aktarimda alan adi 'customTitle'; 'title' eski dokumlerde.
                baslik = str(o.get("customTitle") or o.get("title") or baslik)
                continue
            if tip not in ("user", "assistant"):
                continue
            msg = o.get("message") or {}
            rol = str(msg.get("role") or tip)
            icerik = msg.get("content")
            t = _zaman(o)
            arac_ciktilari = []
            arac_cagrilari = []
            if isinstance(icerik, list):
                for b in icerik:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        arac_cagrilari.append({
                            "id": b.get("id"),
                            "function": {"name": b.get("name") or "",
                                         "arguments": json.dumps(b.get("input") or {},
                                                                 ensure_ascii=False)}})
                    elif b.get("type") == "tool_result":
                        arac_ciktilari.append(b)
            govde = _metin(icerik)
            if govde or arac_cagrilari:
                m = {"role": rol, "content": govde, "timestamp": t}
                if arac_cagrilari:
                    m["tool_calls"] = arac_cagrilari
                if o.get("uuid"):
                    m["id"] = o["uuid"]
                mesajlar.append(m)
            for b in arac_ciktilari:
                c = b.get("content")
                if isinstance(c, list):
                    c = _metin(c)
                mesajlar.append({"role": "tool", "content": c if isinstance(c, str)
                                 else json.dumps(c, ensure_ascii=False),
                                 "timestamp": t, "tool_call_id": b.get("tool_use_id")})
    return mesajlar, baslik


def kaynak(yol, sid=None, baslik=None):
    """birlestir.kaynaklari_birlestir() icin tek kaynak sozlugu."""
    mesajlar, dosya_basligi = oku(yol)
    ad = baslik or dosya_basligi or os.path.basename(os.path.dirname(os.path.abspath(yol)))
    return {"sid": sid or os.path.basename(os.path.dirname(os.path.abspath(yol))),
            "mesaj": mesajlar, "baslik": ad}
