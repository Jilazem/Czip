# -*- coding: utf-8 -*-
"""ccd_dokum.py — Claude Code / Claude Desktop oturum dokumu (transcript .jsonl) -> czip iletleri.

Neden ayri: Hermes oturumlari state.db'de durur, Claude Code oturumlari durmaz
(~/.claude/projects/<proje>/<oturum>.jsonl). Ikisi ayni ise ait olabilir (ayni
proje, ayni gun, biri otekinin devami), ama `czip birlestir` state.db'ye baktigi
icin Claude tarafini goremiyordu. Bu kopru dokumu cevirir; birlestirme kurallari
(sinir isaretleri, zaman sirasi, tekrar ayiklama) degismez.

Blok duzlestirme: assistant iletisindeki 'text' bloklari govde olur, 'tool_use'
bloklari tool_calls'a, user iletisindeki 'tool_result' bloklari ayri 'tool'
iletine dusurulur — czip'in kayit_yap() bekledigi sekil budur.
"""
from __future__ import annotations

import glob
import json
import os

PROJE_DIZIN = os.path.expanduser(
    os.environ.get("CZIP_CLAUDE_PROJELER", "~/.claude/projects"))


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


def claude_dokumu_mu(kayitlar):
    """Satir sozlukleri bir Claude Code dokumune mi ait? (type + message sekli)"""
    for o in kayitlar[:200]:
        if (isinstance(o, dict) and o.get("type") in ("user", "assistant")
                and isinstance(o.get("message"), dict)):
            return True
    return False


def satirlardan(kayitlar):
    """Ayristirilmis dokum satirlari -> (mesajlar, baslik)."""
    mesajlar, baslik = [], ""
    for o in kayitlar:
        if not isinstance(o, dict):
            continue
        tip = o.get("type")
        if tip in ("custom-title", "summary"):
            # Disa aktarimda alan adi 'customTitle'; 'title'/'summary' eski dokumlerde.
            baslik = str(o.get("customTitle") or o.get("title")
                         or o.get("summary") or baslik)
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


def oku(yol):
    """transcript.jsonl -> (mesajlar, baslik). Bozuk satirlar sessizce atlanir."""
    kayitlar = []
    with open(os.path.expanduser(yol), encoding="utf-8", errors="replace") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                kayitlar.append(json.loads(satir))
            except Exception:
                continue
    mesajlar, baslik = satirlardan(kayitlar)
    if not baslik:
        # Ilk gercek kullanici istegi, dosya adindan (uuid) daha okunur bir baslik.
        for m in mesajlar:
            if m["role"] == "user" and m.get("content"):
                baslik = " ".join(str(m["content"]).split())[:60]
                break
    return mesajlar, baslik


def oturumlar(dizin=None, limit=20):
    """En yeni Claude Code oturum dokumleri: [(yol, mtime, bayt)]."""
    d = os.path.expanduser(dizin or PROJE_DIZIN)
    yollar = glob.glob(os.path.join(d, "*", "*.jsonl"))
    yollar.sort(key=os.path.getmtime, reverse=True)
    return [(y, os.path.getmtime(y), os.path.getsize(y)) for y in yollar[:limit]]


def coz(girdi):
    """'cc:son' | 'cc:<uuid>' | 'cc:<yol>' -> dokum yolu (yoksa None)."""
    g = girdi[3:] if girdi.startswith("cc:") else girdi
    if g in ("", "son", "last"):
        o = oturumlar(limit=1)
        return o[0][0] if o else None
    if os.path.isfile(os.path.expanduser(g)):
        return os.path.expanduser(g)
    for y, _, _ in oturumlar(limit=10_000):
        if os.path.basename(y).startswith(g):
            return y
    return None


def kaynak(yol, sid=None, baslik=None):
    """birlestir.kaynaklari_birlestir() icin tek kaynak sozlugu."""
    mesajlar, dosya_basligi = oku(yol)
    ad = baslik or dosya_basligi or os.path.basename(os.path.dirname(os.path.abspath(yol)))
    return {"sid": sid or os.path.splitext(os.path.basename(yol))[0],
            "mesaj": mesajlar, "baslik": ad}
