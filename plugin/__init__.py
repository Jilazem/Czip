# -*- coding: utf-8 -*-
"""/czip + /cunzip — oturumu HKP1 paketine sikistir, yeni oturumda paketi ACMADAN oku.

Motor: $CZIP_HKP (varsayilan ~/czip/hkp.py) (saf stdlib, MCP gerektirmez).
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

_MOTOR = os.environ.get("CZIP_HKP", os.path.expanduser("~/czip/hkp.py"))
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
    _SON["sid"] = sid
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



def _cli(*args: str) -> str:
    """Motoru CLI olarak calistirir (auto/ayar/search/asearch/index/gorev ayni cikti)."""
    import subprocess
    import sys
    ortam = dict(os.environ)
    ortam.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes"))
    try:
        r = subprocess.run([sys.executable, _MOTOR, *args], capture_output=True, text=True,
                           timeout=900, env=ortam, cwd=_motor_dizin())
    except Exception as e:  # noqa: BLE001
        return "❌ czip motoru calistirilamadi: " + str(e)[:200]
    cikti = (r.stdout or "").strip() or (r.stderr or "").strip()
    return cikti[:3500] or "(cikti yok)"


def _son(args: str) -> str:
    """Aktif oturumu paketle + RAG arsivine al + session'i pasife (sonlandir) al.
    Silmek guvenli: icerik pakette, geri alma czip gerial ile."""
    secim = (args or "").strip()
    onceki = _paketle("aktif" if not secim else secim)
    if onceki.startswith("❌"):
        return onceki
    yol = _SON.get("yol")
    if not yol or not os.path.isfile(yol):
        return onceki + "\n⚠️ Paket yolu cozulemedi; oturum ACIK birakildi."
    try:
        kid = _motor().id_ata(yol)  # kayitta mevcut -> ayni id doner
    except Exception:
        kid = None
    _b = _birlestirici()
    sid = _SON.get("sid") or _aktif_session_id()
    try:
        pr = _b.pasife_al([sid], yol, kid, sebep="czip_son")
    except Exception as e:
        return onceki + "\n⚠️ Sonlandirma basarisiz: " + str(e)[:110] + "\n   Paket hazir: " + yol
    if not pr.get("pasif"):
        at = pr.get("atlanan") or [{}]
        return onceki + "\n⚠️ Oturum sonlandirilamadi: " + str(at[0].get("neden", "bilinmeyen"))
    oku = ("   Oku:  czip oku " + kid + "   ara:  czip ara " + kid + " \"sorgu\""
           if kid else "   Oku:  czip read son")
    return "\n".join([
        onceki,
        "",
        "🔒 Oturum sonlandirildi (arsivlendi): " + sid[:16],
        "   Icerik pakette — liste:  czip listele",
        oku,
        "   Geri al:  czip gerial " + os.path.basename(pr["gerial"]),
        "   → artik 'New session' ile temiz oturum acilabilir; liste temiz.",
    ])


def _auto(args: str) -> str:
    """Otomatik paketleme modunu ac/kapat; ardindan guncel ayari gosterir."""
    secim = (args or "on").split()[0].lower()
    if secim not in ("on", "off", "ac", "kapat", "durum"):
        return "Kullanim: /czipauto [on|off]"
    if secim == "durum":
        return _cli("ayar")
    return _cli("auto", {"ac": "on", "kapat": "off"}.get(secim, secim)) + "\n\n" + _cli("ayar")


def _esik(args: str) -> str:
    """Esigi (context yuzdesi) veya kademe listesini ayarlar; argumansiz ayari gosterir."""
    a = (args or "").strip()
    if not a:
        return _cli("ayar")
    if "," in a:
        return _cli("ayar", "kademe", a) + "\n\n" + _cli("ayar")
    return _cli("ayar", "esik", a) + "\n\n" + _cli("ayar")


def _ara(args: str) -> str:
    """Arsivde arama: 'index' depoyu tazeler, 6 haneli ID ile tek pakette, aksi halde tum arsivde."""
    a = (args or "").strip()
    if not a:
        return "Kullanim: /czipara \"<sorgu>\"   |   /czipara <ID> \"<sorgu>\"   |   /czipara index"
    parca = a.split()
    if parca[0].lower() == "index":
        return _cli("index")
    if len(parca) > 1 and len(parca[0]) == 6 and parca[0].isalnum():
        return _cli("search", *parca)
    return _cli("asearch", *parca)


def _gorev(args: str) -> str:
    """Kayitli promt/plan deposu: /czipgorev listele [--tip=plan] | /czipgorev oku <kid|son>."""
    a = (args or "listele").strip()
    return _cli("gorev", *a.split())



def _promt_plan(args: str, tip: str) -> str:
    """Depo komutlarini calistirir; ham gorev verilirse akisi skill'e yonlendirir."""
    a = (args or "").strip()
    ilk = a.split()[0].lower() if a else ""
    if ilk in ("oku", "listele"):
        parca = a.split()
        if ilk == "listele" and not any(x.startswith("--tip") for x in parca):
            parca.append("--tip=" + tip)
        return _cli("gorev", *parca)
    return ("Bu akis model karari ister (ham gorev -> " + tip + " -> onay -> kayit).\n"
            "Komut yerine mesaj olarak yaz:  czip " + tip + " <ham gorev metni>\n"
            "Depo komutlari: /czip_" + tip + " listele   |   /czip_" + tip + " oku <kid|son>")



_YARDIM = """📦 czip — oturum paketleme ve arşiv komutları

PAKETLE
  /czip [son|aktif|<id>|<dosya>] [--oto] [--oto-pasif] [--jev] [--eksiksiz]
      Oturumu .hkp paketine sıkıştırır. --oto: aynı işi yapan oturumları da
      aynı pakete alır. --oto-pasif: kaynakları ayrıca pasife alır.
      --jev: büyük araç çıktılarını karar kapısına sorar (motor: Laya, yerel).
      --eksiksiz: kayıpsız mod (kırpma ve budama yok).

OKU (paketi açmadan)
  /czip_ex [<paket|ID|son>] [bas-bit]     (/czipex, /cunzip aynı işi yapar)
      Harita + son iletiler; aralık verirsen o iletilerin tam metni.

ARA
  /czip_search "<sorgu>"          tüm arşivde ara      (/czipara)
  /czip_search <ID> "<sorgu>"     tek pakette ara
  /czip_search index              arşiv indeksini tazele

BİRLEŞTİR
  /czip_merge [bak|oto|<id1> <id2> ...] [--jev] [--gun=7]      (/czipmerge)
      Aynı işi yapan oturumları tek pakete alır; kararı karar kapısı verir.

OTOMATİK MOD
  /czip_auto [on|off|durum]       (/czipauto)   eşiği geçince sormadan paketler
  /czip_esik [50 | 50,75,90]      (/czipesik)   eşik = context penceresinin yüzdesi

GÖREV DEPOSU (kalıcı promt/plan)
  czip promt <ham görev>          mesaj olarak yaz → prompta çevirir, onay alır, kaydeder
  czip plan  <ham görev>          aynısı ama önce keşfedip adım adım plan çıkarır
  /czip_promt listele | oku <kid|son>          kayıtlı promtlar
  /czip_plan  listele | oku <kid|son>          kayıtlı planlar
  /czip_gorev listele [--tip=plan|promt]       hepsi tek listede

NOTLAR
  • Paketin tamamı asla bağlama yüklenmez; oku/ara/aralık ile parça parça okunur.
  • Kaynak oturumlara dokunulmaz (salt-okuma); --oto-pasif hariç.
  • Karar kapısı Laya ile yerel çalışır (anahtar istemez); Türkçe içerik node1 ile
    İngilizceye çevrilip sorulur. CZIP_KARAR_MOTORU=jev ile eski motora dönülür.
  • Yardım: /czip_help"""


def _yardim_metni(args: str = "") -> str:
    return _YARDIM


def register(ctx: Any) -> None:
    """Hermes'e /czip ve /cunzip komutlarini kaydeder."""
    ctx.register_command(
        "czip",
        lambda args="": _paketle(args),
        description="Pack the session into a .hkp archive — auto-merges same-job sessions.",
        args_hint="[son|aktif|<id>] [--oto] [--oto-pasif] [--jev] [--eksiksiz]",
    )
    ctx.register_command(
        "czip-son",
        lambda args="": _son(args),
        description="Pack + archive the session and end it — safe to delete, undo: czip gerial.",
        args_hint="[<id>]  (default: active session)",
    )
    ctx.register_command(
        "czip_end",
        lambda args="": _son(args),
        description="Pack + archive + end (alias of /czip-son).",
        args_hint="[<id>]  (default: active session)",
    )
    # Gateway slash lookup '_' -> '-' normalize eder: /czip_end için tireli kayıt ŞART.
    ctx.register_command(
        "czip-end",
        lambda args="": _son(args),
        description="Pack + archive + end (alias of /czip-son).",
        args_hint="[<id>]  (default: active session)",
    )
    ctx.register_command(
        "cunzip",
        lambda args="": _okur(args),
        description="(alias) /czip_ex",
        args_hint="<takma ad — /czip_ex>",
    )
    ctx.register_command(
        "czipmerge",
        lambda args="": _birlestir(args),
        description="(alias) /czip_merge",
        args_hint="<takma ad — /czip_merge>",
    )
    ctx.register_command(
        "czipauto",
        lambda args="": _auto(args),
        description="(alias) /czip_auto",
        args_hint="<takma ad — /czip_auto>",
    )
    ctx.register_command(
        "czipesik",
        lambda args="": _esik(args),
        description="(alias) /czip_esik",
        args_hint="<takma ad — /czip_esik>",
    )
    ctx.register_command(
        "czipara",
        lambda args="": _ara(args),
        description="(alias) /czip_search",
        args_hint="<takma ad — /czip_search>",
    )
    ctx.register_command(
        "czipgorev",
        lambda args="": _gorev(args),
        description="(alias) /czip_gorev",
        args_hint="<takma ad — /czip_gorev>",
    )
    ctx.register_command("czip_help", lambda args="": _yardim_metni(args),
                         description="List every czip command with one-line usage.", args_hint="")
    for _ad in ("cziphelp", "czipyardim"):
        ctx.register_command(
            _ad,
            lambda args="": _yardim_metni(args),
            description="(alias) /czip_help",
            args_hint="<takma ad — /czip_help>",
        )
    # Claude Code'daki adlar (alt cizgili) Telegram/CLI'da da calissin — ayni islevler.
    for _ad, _fn, _ac, _ip in (
        ("czip_auto", _auto, "Turn automatic packing on/off (alias of /czipauto).", "[on|off|durum]"),
        ("czip_esik", _esik, "Set the auto-pack threshold (alias of /czipesik).", "[50 | 50,75,90]"),
        ("czip_search", _ara, "Search the pack archive (alias of /czipara).", "[\"<sorgu>\"] | <ID> \"<sorgu>\" | index"),
        ("czip_ex", _okur, "Read a pack without unpacking (alias of /czipex).", "[<paket.hkp|ID|son>] [bas-bit]"),
        ("czip_merge", _birlestir, "Merge same-job sessions (alias of /czipmerge).", "[bak|oto|<id1> <id2> ...]"),
        ("czip_gorev", _gorev, "Saved prompt/plan store (alias of /czipgorev).", "listele [--tip=plan|promt] | oku <kid|son>"),
    ):
        ctx.register_command(_ad, (lambda f: lambda args="": f(args))(_fn), description=_ac, args_hint=_ip)
    # Gateway slash lookup '_' -> '-' normalize eder; tireli formlar da kayitli olsun (Telegram /czip_auto fix).
    for _ad, _fn, _ac, _ip in (
        ("czip-auto", _auto, "Turn automatic packing on/off.", "[on|off|durum]"),
        ("czip-esik", _esik, "Set the auto-pack threshold.", "[50 | 50,75,90]"),
        ("czip-search", _ara, "Search the pack archive.", "[\"<sorgu>\"] | <ID> \"<sorgu>\" | index"),
        ("czip-ex", _okur, "Read a pack without unpacking.", "[<paket.hkp|ID|son>] [bas-bit]"),
        ("czip-merge", _birlestir, "Merge same-job sessions.", "[bak|oto|<id1> <id2> ...]"),
        ("czip-gorev", _gorev, "Saved prompt/plan store.", "listele [--tip=plan|promt] | oku <kid|son>"),
        ("czip-help", _yardim_metni, "List every czip command with one-line usage.", ""),
        ("czip-promt", lambda a="": _promt_plan(a, "promt"), "Saved prompt store.", "listele | oku <kid|son>"),
        ("czip-plan", lambda a="": _promt_plan(a, "plan"), "Saved plan store.", "listele | oku <kid|son>"),
    ):
        ctx.register_command(_ad, (lambda f: lambda args="": f(args))(_fn), description=_ac, args_hint=_ip)
    ctx.register_command(
        "czip_promt",
        lambda args="": _promt_plan(args, "promt"),
        description="Saved prompt store; the convert-and-approve flow runs as a message (czip promt ...).",
        args_hint="listele | oku <kid|son>",
    )
    ctx.register_command(
        "czip_plan",
        lambda args="": _promt_plan(args, "plan"),
        description="Saved plan store; the convert-and-approve flow runs as a message (czip plan ...).",
        args_hint="listele | oku <kid|son>",
    )
    # /czip yazinca hepsi cikacak diye ortak onek; eski adlar da calismaya devam eder.
    ctx.register_command(
        "czipex",
        lambda args="": _okur(args),
        description="(alias) /czip_ex",
        args_hint="<takma ad — /czip_ex>",
    )
    # cunzip zaten yukarida kayitli; yalniz cmerge'in eski adi koprulenir.
    for _eski, _fn, _ip in (("cmerge", _birlestir, "[bak|oto|<id1> <id2> ...]"),):
        ctx.register_command(
            _eski,
            (lambda f: lambda args="": f(args))(_fn),
            description="(legacy alias — use czipex/czipmerge)",
            args_hint=_ip,
        )
