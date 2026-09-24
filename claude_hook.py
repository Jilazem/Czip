#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude_hook.py — Claude Code / Claude Desktop icin czip otopilotu (hook).

Claude Code bu betigi olaylarda cagirir (stdin: olay JSON'u):

  SessionStart      -> BRIFING: bu projede son yapilanlar + son paketin yon karti.
                       compact sonrasi: [CZIP BAGLANTISI] (compaction'da kaybolan
                       her sey pakette). Tembel bakim: indeks + haftalik temizlik.
  UserPromptSubmit  -> BAGLAM KORUMA: gercek baglam doluluğu (transcript'teki son
                       usage) esigi gecince oturumu kayipsiz paketler ve AI'ya
                       "sisirme" talimati verir. HATIRLATMA: istekle ilgili gecmis
                       isi (RAG, alaka kapili) kisa satirlarla hatirlatir.
  PreCompact        -> compaction'dan ONCE tum oturumu paketler (kayip sifir).

Kural: hook ASLA oturumu bozmaz. Her hata yutulur ve hook.log'a yazilir;
cikis kodu daima 0. Cikti yalniz gerektiginde (bos = sessiz tur).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

KOK = os.path.dirname(os.path.abspath(__file__))
if KOK not in sys.path:
    sys.path.insert(0, KOK)

import hkp  # noqa: E402

VARSAYILAN_CTX = 200_000
BUYUK_CTX = 1_000_000
KUYRUK_BAYT = 2 * 1024 * 1024


def durum_dizini():
    return os.path.expanduser(os.environ.get("CZIP_HOOK_DURUM", "~/.hermes/czip/claude-oturum"))


def _log(metin):
    try:
        d = durum_dizini()
        os.makedirs(d, exist_ok=True)
        y = os.path.join(os.path.dirname(d), "hook.log")
        if os.path.exists(y) and os.path.getsize(y) > 512 * 1024:
            os.replace(y, y + ".1")
        with open(y, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + metin + "\n")
    except Exception:  # noqa: BLE001
        pass


def _durum_oku(sid):
    try:
        with open(os.path.join(durum_dizini(), "%s.json" % re.sub(r"[^\w-]", "_", sid)),
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _durum_yaz(sid, d):
    try:
        os.makedirs(durum_dizini(), exist_ok=True)
        y = os.path.join(durum_dizini(), "%s.json" % re.sub(r"[^\w-]", "_", sid))
        with open(y + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(y + ".tmp", y)
    except Exception as e:  # noqa: BLE001
        _log("durum_yaz: %s" % e)


def _ayar():
    try:
        import ayar
        return ayar.oku()
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ olcum
def baglam_olc(transcript):
    """Transcript'ten GERCEK baglam boyutu: son asistan iletisinin usage'i.

    Doner: (token, kaynak) — kaynak 'usage' (kesin) ya da 'tahmin' (bayt/4)."""
    if not transcript or not os.path.isfile(transcript):
        return 0, "yok"
    boy = os.path.getsize(transcript)
    with open(transcript, "rb") as f:
        if boy > KUYRUK_BAYT:
            f.seek(-KUYRUK_BAYT, os.SEEK_END)
        kuyruk = f.read().decode("utf-8", "replace")
    for satir in reversed(kuyruk.splitlines()):
        if '"usage"' not in satir:
            continue
        try:
            o = json.loads(satir)
        except ValueError:
            continue
        u = (o.get("message") or {}).get("usage") if isinstance(o, dict) else None
        if isinstance(u, dict) and not o.get("isSidechain"):
            t = sum(int(u.get(k) or 0) for k in
                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            if t:
                return t + int(u.get("output_tokens") or 0), "usage"
    return boy // 4, "tahmin"


def _pencere(token, a):
    ctx = int(a.get("claude_ctx") or VARSAYILAN_CTX)
    # olculen kullanim pencereyi asiyorsa model buyuk pencereli (1M) demektir
    return BUYUK_CTX if token > ctx else ctx


def _kademeler(ctx, a):
    ks = set(float(k) for k in (a.get("kademeler") or [0.5, 0.75, 0.9]))
    try:
        import ayar
        ks.add(round(ayar.esik_token(ctx, a) / float(ctx), 3))
    except Exception:  # noqa: BLE001
        pass
    return sorted(k for k in ks if 0 < k < 1)


# ------------------------------------------------------------------ paketleme
def transcript_paketle(transcript, session_id, cwd, sebep):
    """Claude transcript'ini paketler + gunluge/indekse yazar. Doner: (kid, yol)."""
    import ccd_dokum
    import hafiza
    mesajlar, baslik = ccd_dokum.oku(transcript)
    if not mesajlar:
        return None, None
    kaynak = {"sid": "cc:" + str(session_id), "cwd": cwd,
              "proje": os.path.basename((cwd or "").rstrip("/")), "sebep": sebep}
    d = os.path.expanduser(hkp.PAKET_DIZIN)
    os.makedirs(d, exist_ok=True)
    temiz = re.sub(r"[^A-Za-z0-9-]+", "-", (baslik or "claude")[:40]).strip("-") or "claude"
    yol = os.path.join(d, "CC-%s-%s.hkp" % (temiz, time.strftime("%Y%m%d-%H%M%S")))
    r = hkp.sikistir(mesajlar, yol, "akilli", baslik=baslik or temiz,
                     kaynak_bayt=os.path.getsize(transcript),
                     jev=bool(_ayar().get("hook_laya")), kaynak=kaynak)
    kid = hkp.id_ata(r["yol"], baslik or temiz)
    hafiza.kaydet(r, kid, kaynak)
    return kid, r["yol"]


# ------------------------------------------------------------------ olaylar
def _bakim(a):
    """Tembel bakim: indeksi tazele (3 sn butce), haftalik temizligi arka planda baslat."""
    satir = []
    try:
        import depo
        depo.guncelle(butce_sn=3)
    except Exception as e:  # noqa: BLE001
        _log("bakim/indeks: %s" % e)
    try:
        import temizlik
        if a.get("haftalik_temizlik", True) and temizlik.gerekli_mi():
            temizlik.baslat_isareti()
            subprocess.Popen([sys.executable, os.path.join(KOK, "hkp.py"), "temizle",
                              "--uygula", "--sessiz"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            satir.append("(czip: haftalik temizlik arka planda basladi — geri almak: "
                         "czip temizle geri)")
    except Exception as e:  # noqa: BLE001
        _log("bakim/temizlik: %s" % e)
    return satir


def session_start(o):
    a = _ayar()
    sid, cwd, kaynak = o.get("session_id") or "?", o.get("cwd"), o.get("source") or "startup"
    d = _durum_oku(sid)
    parca = []
    if kaynak == "compact" and d.get("paketler"):
        kid = d["paketler"][-1]
        parca.append("[CZIP BAGLANTISI] Compaction oncesi oturumun TAMAMI paketlendi: ID %s. "
                     "Ozette eksik kalan her ayrinti orada: czip ara %s \"...\" -> "
                     "czip aralik %s <i>. Tahmin etme, gerekirse oku." % (kid, kid, kid))
        try:
            import yon
            meta = hkp.meta_oku(hkp.id_coz(kid))
            if meta.get("yon"):
                parca.append(yon.metin(meta["yon"], kid))
        except Exception as e:  # noqa: BLE001
            _log("compact/yon: %s" % e)
    elif kaynak in ("startup", "clear") and a.get("brifing", True):
        import hafiza
        b = hafiza.brifing(cwd=cwd, n=int(a.get("brifing_n", 3)))
        if b:
            parca.append(b)
    if kaynak in ("compact", "clear"):
        d["kademeler"] = []  # baglam kuculdu: koruma yeniden devreye girebilir
        d["auto_seviye"] = 0
        _durum_yaz(sid, d)
    parca.extend(_bakim(a))
    return "\n\n".join(parca)


def user_prompt(o):
    a = _ayar()
    sid, cwd = o.get("session_id") or "?", o.get("cwd")
    tr = o.get("transcript_path")
    d = _durum_oku(sid)
    parca = []
    # 1) BAGLAM KORUMA
    if a.get("koruma", True) and tr and int(a.get("adim_token") or 0) > 0:
        parca.extend(_auto_adim(o, a, d, tr))
    elif a.get("koruma", True) and tr:
        token, kaynak = baglam_olc(tr)
        ctx = _pencere(token, a)
        oran = token / float(ctx) if ctx else 0
        gecilen = [k for k in _kademeler(ctx, a) if oran >= k]
        ateslenen = set(d.get("kademeler") or [])
        if gecilen and gecilen[-1] not in ateslenen:
            k = gecilen[-1]
            try:
                kid, yol = transcript_paketle(tr, sid, cwd, "koruma-%d" % round(k * 100))
            except Exception as e:  # noqa: BLE001
                kid, yol = None, None
                _log("koruma/paketle: %s" % e)
            d["kademeler"] = sorted(ateslenen | set(gecilen))
            if kid:
                d.setdefault("paketler", []).append(kid)
                d.setdefault("paket_yollari", []).append(yol)
            yuzde = round(oran * 100)
            t = ("[CZIP BAGLAM KORUMA] Baglam ~%%%d dolu (%dk/%dk token, %s). " %
                 (yuzde, token // 1000, ctx // 1000, kaynak))
            if kid:
                t += "Oturum kayipsiz paketlendi: ID %s — compaction'da hicbir sey kaybolmaz. " % kid
            t += ("Baglami sisirme: dosyayi bastan sona okuma (grep / offset+limit kullan), "
                  "uzun komut ciktilarini head/tail ile sinirla, eski konusmayi yeniden "
                  "okumak yerine czip ara %s \"...\" ile hedefli cek." % (kid or "<id>"))
            if k >= 0.9 or oran >= 0.9:
                t += " KRITIK: su anki alt isi bitirir bitirmez kullaniciya /compact oner."
            elif k >= 0.75:
                t += " Bir alt is bittiginde kullaniciya /compact onermeyi dusun."
            parca.append(t)
    # 2) HATIRLATMA (RAG)
    istek = o.get("prompt") or ""
    if a.get("hatirlatma", True) and len(istek) >= 12 and not istek.lstrip().startswith("/"):
        import hafiza
        metin, isabet = hafiza.hatirlatma(
            istek, haric=set(d.get("paket_yollari") or []),
            limit=int(a.get("hatirlatma_max", 3)), atla=d.get("hatirlanan") or (),
            haric_sid="cc:" + str(sid))
        if isabet:
            parca.append(metin)
            d["hatirlanan"] = (list(d.get("hatirlanan") or []) +
                               ["%s#%s" % (h["path"], h["i"]) for h in isabet])[-200:]
    _durum_yaz(sid, d)
    return "\n\n".join(parca)


def _auto_adim(o, a, d, tr):
    """czip-autoN: baglam her N bin token buyudukce kayipsiz paketle.

    Seviye = token // adim. Seviye artinca (64k, 128k, 192k ...) bir kez paketler;
    /compact ya da /clear sonrasi seviye sifirlanir (SessionStart)."""
    import ayar
    sid, cwd = o.get("session_id") or "?", o.get("cwd")
    token, kaynak = baglam_olc(tr)
    seviye = ayar.auto_seviye(token, a)
    if seviye <= int(d.get("auto_seviye") or 0):
        return []
    adim_k = int(a["adim_token"]) // 1000
    try:
        kid, yol = transcript_paketle(tr, sid, cwd, "auto%d-%d" % (adim_k, seviye))
    except Exception as e:  # noqa: BLE001
        kid, yol = None, None
        _log("auto/paketle: %s" % e)
    d["auto_seviye"] = seviye
    if kid:
        d.setdefault("paketler", []).append(kid)
        d.setdefault("paket_yollari", []).append(yol)
    ctx = _pencere(token, a)
    t = ("[CZIP-AUTO%d] Baglam %dk token'i gecti (%dk, %s). " %
         (adim_k, seviye * adim_k, token // 1000, kaynak))
    if kid:
        t += "Oturum kayipsiz paketlendi: ID %s. " % kid
    t += ('Bu noktaya kadarki her ayrinti pakette: gerekirse czip ara %s "..." / '
          "czip aralik %s <i>. Baglami sisirme (grep / offset+limit, head/tail)." %
          (kid or "<id>", kid or "<id>"))
    if token >= 0.75 * ctx:
        t += " Pencere doluyor (%%%d): alt is bitince kullaniciya /compact oner." % round(100.0 * token / ctx)
    return [t]


def pre_compact(o):
    sid, tr = o.get("session_id") or "?", o.get("transcript_path")
    if not tr:
        return ""
    kid, yol = transcript_paketle(tr, sid, o.get("cwd"), "precompact-%s" % (o.get("trigger") or "?"))
    if kid:
        d = _durum_oku(sid)
        d.setdefault("paketler", []).append(kid)
        d.setdefault("paket_yollari", []).append(yol)
        _durum_yaz(sid, d)
    return ""  # PreCompact ciktisi baglama girmez; baglanti SessionStart(compact)'ta


OLAYLAR = {"SessionStart": session_start, "UserPromptSubmit": user_prompt,
           "PreCompact": pre_compact}


def calistir(olay, veri):
    """Olay isle -> Claude Code'a yazilacak JSON metni ('' = sessiz)."""
    fn = OLAYLAR.get(olay)
    if not fn:
        return ""
    try:
        metin = fn(veri)
    except Exception as e:  # noqa: BLE001
        _log("%s: %s: %s" % (olay, type(e).__name__, e))
        return ""
    if not metin or olay == "PreCompact":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": olay,
                                              "additionalContext": metin}},
                      ensure_ascii=False)


def main(argv):
    try:
        veri = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        veri = {}
    olay = (argv[0] if argv else "") or veri.get("hook_event_name", "")
    cikti = calistir(olay, veri)
    if cikti:
        sys.stdout.write(cikti + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
