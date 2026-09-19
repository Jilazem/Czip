# -*- coding: utf-8 -*-
"""/czip + /cunzip — oturumu HKP1 paketine sikistir, yeni oturumda paketi ACMADAN oku.

Motor: ayni depodaki hkp.py (saf stdlib, MCP gerektirmez).
  /czip                 aktif oturumu paketle + sonraki adim komutlarini yazdir
  /czip son             en son aktif oturumu paketle
  /czip <id>            baska oturumu paketle (onek eslesmesi olur)
  /czip <id> --eksiksiz kirpmasiz mod
  /czip <id> --jev    buyuk arac ciktilarini Jev'e sor; oluler paketten cikar
  /cunzip <paket.hkp>   INDEKS + son 6 ilet + durum (~30 KB; tam paket baglama GIRMEZ)
  /cunzip <paket> <a-b> yalniz o araliktaki iletileri tam metin ver
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime
from typing import Any

_MOTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hkp.py")
_SON = {"yol": None}


def _motor():
    spec = importlib.util.spec_from_file_location("hkp_motor", _MOTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("hkp motoru bulunamadi: " + _MOTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _aktif_session_id() -> str:
    import sqlite3
    db = os.path.expanduser(os.environ.get("HERMES_STATE_DB", "~/.hermes/state.db"))
    con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT session_id FROM messages GROUP BY session_id "
                          "ORDER BY MAX(COALESCE(timestamp,0)) DESC LIMIT 1").fetchone()
        return str(row[0]) if row else ""
    finally:
        con.close()


def _slug(baslik: str) -> str:
    izin = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJ...0123456789")
    s = "".join(ch if ch in izin else "-" for ch in (baslik or "oturum")[:40])
    return "-".join(p for p in s.split("-") if p) or "oturum"


def _paketle(args: str) -> str:
    m = _motor()
    parca = args.split()
    mod = "eksiksiz" if "--eksiksiz" in parca else "akilli"
    jev = "--jev" in parca
    idler = [p for p in parca if not p.startswith("--")]
    secim = idler[0] if idler else "son"
    if secim == "aktif":
        secim = _aktif_session_id()
    try:
        sid, mesajlar, baslik = m.oturum_oku(secim)
    except Exception as e:
        return "❌ /czip hatasi: " + str(e)
    if not mesajlar:
        return "❌ Oturum bos: " + secim
    paket_dizin = os.path.expanduser(m.PAKET_DIZIN)
    os.makedirs(paket_dizin, exist_ok=True)
    ad = _slug(baslik) + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".hkp"
    yol = os.path.join(paket_dizin, ad)
    r = m.sikistir(mesajlar, yol, mod=mod, baslik=baslik, jev=jev)
    _SON["yol"] = r["yol"]
    kirp = ""
    if r.get("eksik_bildirim"):
        kirp = " (kirpilan {} araç çıktısı — eksiksiz modla tam kalır)".format(len(r["eksik_bildirim"]))
    jv = r.get("jev") or {}
    jtxt = "" if not jv else " | Jev: sil {} / tut {} / kirp {}".format(
        jv.get("sil", 0), jv.get("tut", 0), jv.get("kirp", 0))
    return "\n".join([
        "📦 Oturum paketlendi: " + (baslik or sid),
        "   {} ilet | {:,} -> {:,} B ({:.1f}x) | sozluk {} | parca {}{}{}".format(
            r["mesaj"], r["kaynak_bayt"], r["paket_bayt"], r["oran"],
            r["sozluk"], r["parca"], kirp, jtxt),
        "   paket: " + r["yol"],
        "",
        "Sonraki adım — yeni oturumda (paket ACILMAZ, sadece okunur):",
        "   /new " + (baslik or sid)[:55],
        "   /cunzip " + r["yol"],
        "Detay gereken yer için: /cunzip " + r["yol"] + " <bas-bit>",
    ])


def _okur(args: str) -> str:
    m = _motor()
    tok = args.split()
    if not tok:
        yol = _SON["yol"] or m.son_paket()
        if not yol or not os.path.isfile(yol):
            return "❌ Paket yok. Önce /czip ile paket üret, ya da /cunzip <paket.hkp> ver."
        tok = [yol]
    dosya = tok[0]
    if not os.path.isfile(os.path.expanduser(dosya)):
        return "❌ Paket yok: " + dosya
    try:
        if len(tok) == 1:
            g = m.kilavuz(dosya)
            satirlar = ["📚 PAKET KILAVUZU — {} ({} ilet, paket: {})".format(
                g["baslik"], g["toplam"], os.path.basename(dosya)), ""]
            satirlar.append("INDEKS (i|rol|araç|özet):")
            for s in g["indeks"]:
                satirlar.append("{}|{}|{}|{}".format(
                    s["i"], s["rol"], ",".join(s.get("arac", [])), s.get("ozet", "")))
            satirlar.append("")
            satirlar.append("SON {} ILET (tam):".format(len(g["son"])))
            for it in g["son"]:
                satirlar.append(json.dumps(it, ensure_ascii=False))
            satirlar.append("")
            d = g["durum"]
            satirlar.append("DURUM: son_rol={} kullanıcı yanıtı bekliyor={}".format(
                d.get("son_rol"), d.get("kullanici_yaniti_bekliyor")))
            satirlar.append("")
            satirlar.append("KURAL: " + g["talimat"])
            return "\n".join(satirlar)
        iletler = m.mesaj_araligi(dosya, tok[1])
        return "📖 {} [{}] — {} ilet:\n".format(os.path.basename(dosya), tok[1], len(iletler)) + \
            "\n".join(json.dumps(it, ensure_ascii=False) for it in iletler)
    except Exception as e:
        return "❌ /cunzip hatası: " + str(e)


def register(ctx: Any) -> None:
    """Hermes'e /czip ve /cunzip komutlarini kaydeder."""
    ctx.register_command(
        "czip",
        lambda args="": _paketle(args),
        description="Oturumu .hkp paketine sıkıştır (yeni oturuma taşımadan önce).",
        args_hint="[son|aktif|session_id] [--eksiksiz]",
    )
    ctx.register_command(
        "cunzip",
        lambda args="": _okur(args),
        description="Paketi ACAMDAN oku: indeks + son ilet + istenen aralık.",
        args_hint="<paket.hkp> [bas-bit]",
    )
