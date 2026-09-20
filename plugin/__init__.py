# -*- coding: utf-8 -*-
"""/czip + /cunzip — oturumu HKP1 paketine sikistir, yeni oturumda paketi ACMADAN oku.

Motor: ~/007-HERMES/10-MCP-SERVERS/oturum-sikistirici/hkp.py (saf stdlib, MCP gerektirmez).
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

_MOTOR = os.path.expanduser("~/007-HERMES/10-MCP-SERVERS/oturum-sikistirici/hkp.py")
_SON = {"yol": None}


def _motor_dizin():
    return os.path.dirname(_MOTOR)


def _yukle(ad, yol):
    """Motor dizinini sys.path'e alip modulu yukler (birlestir.py 'import hkp' yapar)."""
    import sys
    d = _motor_dizin()
    if d not in sys.path:
        sys.path.insert(0, d)
    if ad in sys.modules:
        return sys.modules[ad]
    spec = importlib.util.spec_from_file_location(ad, yol)
    if spec is None or spec.loader is None:
        raise RuntimeError("motor bulunamadi: " + yol)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ad] = mod
    spec.loader.exec_module(mod)
    return mod


def _motor():
    return _yukle("hkp", _MOTOR)


def _birlestirici():
    _motor()  # hkp once yuklensin, birlestir onu import ediyor
    return _yukle("birlestir", os.path.join(_motor_dizin(), "birlestir.py"))


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


def _birlestir(args: str) -> str:
    """/cmerge — ayni isi yapan oturumlari tek pakete birlestirir."""
    try:
        b = _birlestirici()
        m = _motor()
    except Exception as e:
        return "❌ /cmerge motoru yuklenemedi: " + str(e)

    parca = args.split()
    jev = "--jev" in parca
    mod = "eksiksiz" if "--eksiksiz" in parca else "akilli"
    gun = 7
    for p in parca:
        if p.startswith("--gun="):
            try:
                gun = max(1, int(p.split("=", 1)[1]))
            except ValueError:
                pass
    idler = [p for p in parca if not p.startswith("--") and p not in ("oto", "bak")]
    oto = "oto" in parca
    sadece_bak = "bak" in parca or (not idler and not oto)

    # --- ADAY BULMA / RAPOR MODU -------------------------------------------
    if sadece_bak or oto:
        try:
            _, ciftler = b.adaylari_bul(gun=gun)
        except Exception as e:
            return "❌ /cmerge aday taramasi: " + str(e)
        if not ciftler:
            return "✅ Son {} gunde birlestirilecek benzer oturum bulunamadi.".format(gun)
        gruplar, iz = b.gruplari_kur(ciftler, jev=jev)
        satir = ["🔎 /cmerge aday taramasi — son {} gun, {} benzer cift".format(gun, len(ciftler))]
        jb = (iz.get("jev") or {})
        if jev:
            satir.append("   Jev karar kapisi: " + (
                "HATA ({}) — yerel benzerlige dusuldu".format(jb.get("hata"))
                if jb.get("hata") else
                "{} soru / {} cevap".format(jb.get("soru"), jb.get("cevap"))))
        satir.append("")
        for k in iz["kararlar"][:12]:
            satir.append("   {} {} <-> {}  {}={:.2f}".format(
                "✅" if k["kabul"] else "⬜",
                k["a"], k["b"], k["kaynak"], k["skor"]))
            satir.append("        A: " + k["a_ozet"])
            satir.append("        B: " + k["b_ozet"])
        satir.append("")
        if not gruplar:
            satir.append("Esigi gecen grup yok — birlestirme yapilmadi.")
            if not jev:
                satir.append("Ipucu: --jev ile Jev karar kapisina sordurabilirsin.")
            return "\n".join(satir)
        satir.append("BIRLESTIRILEBILIR GRUPLAR:")
        for i, g in enumerate(gruplar, 1):
            satir.append("   {}) {}".format(i, "  ".join(g)))
        if not oto:
            satir.append("")
            satir.append("Uygulamak icin:  /cmerge " + " ".join(gruplar[0]) +
                         (" --jev" if jev else ""))
            return "\n".join(satir)
        idler = gruplar[0]
        satir.append("")
        satir.append("oto: 1. grup birlestiriliyor...")
        onsoz = satir
    else:
        onsoz = []

    if len(idler) < 2:
        return ("❌ /cmerge en az 2 oturum ister.\n"
                "   /cmerge            -> aday tara (degistirmez)\n"
                "   /cmerge oto --jev  -> en guclu grubu Jev onayiyla birlestir\n"
                "   /cmerge <id1> <id2> [--jev] [--eksiksiz]")

    # --- BIRLESTIR + PAKETLE ------------------------------------------------
    try:
        mesajlar, baslik, rapor = b.oturumlari_birlestir(idler)
    except Exception as e:
        return "\n".join(onsoz + ["❌ /cmerge birlestirme hatasi: " + str(e)])
    if not mesajlar:
        return "❌ Birlesik oturum bos."

    paket_dizin = os.path.expanduser(m.PAKET_DIZIN)
    os.makedirs(paket_dizin, exist_ok=True)
    ad = "BIRLESIK-" + _slug(baslik) + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".hkp"
    yol = os.path.join(paket_dizin, ad)
    try:
        r = m.sikistir(mesajlar, yol, mod=mod, baslik=baslik, jev=jev)
    except Exception as e:
        return "\n".join(onsoz + ["❌ /cmerge sikistirma hatasi: " + str(e)])
    _SON["yol"] = r["yol"]

    jv = r.get("jev") or {}
    jtxt = "" if not jv or jv.get("hata") else " | arac budama: sil {} / tut {} / kirp {}".format(
        jv.get("sil", 0), jv.get("tut", 0), jv.get("kirp", 0))
    satirlar = onsoz + [
        "",
        "🔗 {} oturum TEK pakette birlestirildi: {}".format(rapor["oturum"], baslik),
    ]
    for d in rapor["ayrinti"]:
        satirlar.append("   • {} — {} ilet — {}".format(d["sid"], d["ilet"], (d["baslik"] or "")[:50]))
    satirlar += [
        "   {} ilet -> {} ilet (yinelenen atilan: {})".format(
            rapor["kaynak_ilet"], rapor["birlesik_ilet"], rapor["yinelenen_atilan"]),
        "   {:,} -> {:,} B ({:.1f}x) | sozluk {} | parca {}{}".format(
            r["kaynak_bayt"], r["paket_bayt"], r["oran"], r["sozluk"], r["parca"], jtxt),
        "   paket: " + r["yol"],
        "",
        "Sonraki adim — TEK yeni oturumda devam et:",
        "   /new " + baslik[:55],
        "   /cunzip " + r["yol"],
        "",
        "NOT: kaynak oturumlar state.db'de DOKUNULMADAN duruyor; bu islem salt-okunur.",
    ]
    return "\n".join(satirlar)


def register(ctx: Any) -> None:
    """Hermes'e /czip ve /cunzip komutlarini kaydeder."""
    ctx.register_command(
        "czip",
        lambda args="": _paketle(args),
        description="Pack the session into a .hkp archive — auto-merges same-job sessions.",
        args_hint="[son|aktif|<id>] [--oto] [--oto-pasif] [--jev] [--eksiksiz]",
    )
    ctx.register_command(
        "cunzip",
        lambda args="": _okur(args),
        description="Read a pack WITHOUT unpacking: map + last messages + requested range.",
        args_hint="<paket.hkp> [bas-bit]",
    )
    ctx.register_command(
        "czipmerge",
        lambda args="": _birlestir(args),
        description="Merge 2+ sessions doing the same job into ONE .hkp pack (gated by a Jev verdict).",
        args_hint="[bak|oto|<id1> <id2> ...] [--jev] [--eksiksiz] [--gun=7]",
    )
    # /czip yazinca hepsi cikacak diye ortak onek; eski adlar da calismaya devam eder.
    ctx.register_command(
        "czipex",
        lambda args="": _okur(args),
        description="Expand/read a pack — via RAG, without loading the full dump.",
        args_hint="<paket.hkp|ID|son> [bas-bit]",
    )
    # cunzip zaten yukarida kayitli; yalniz cmerge'in eski adi koprulenir.
    for _eski, _fn, _ip in (("cmerge", _birlestir, "[bak|oto|<id1> <id2> ...]"),):
        ctx.register_command(
            _eski,
            (lambda f: lambda args="": f(args))(_fn),
            description="(legacy alias — use czipex/czipmerge)",
            args_hint=_ip,
        )
