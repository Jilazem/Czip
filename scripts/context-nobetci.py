#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# context-nobetci.py — oturum context şişmesini izler; eşik aşılınca ilgili
# Telegram sohbetine butonlu uyarı gönderir. Butona basınca sohbete normal
# mesaj düşer ve czip plugin'inin pre_gateway_dispatch hook'u yakalar:
# paketle + yeni oturuma taşı işi tek tıkla biter (gateway çekirdeği değişmez).
#
# Cron (no-agent): her 15 dk. stdout = teslim edilecek metin; boş = sessiz.
#   Uyarılar kendi kanalına (o oturumun thread'ine) gönderilir; stdout'a yalnız
#   hata/özet yazılır.
#
# Ölçüm: sessions.model_config -> _usage_anchor.prompt_tokens (canlı bağlamın
# Hermes kendi ölçtüğü anlık değeri) + tahmini sistem payı. İkinci ölçüt:
# messages içerik baytı/4 (kirpık yok, kabaca yüksek kalır). MAX kullanılır.
# Eşik: ESİK_TOKEN (150k) VEYA context_length x ESİK_ORAN (%30) — ilk aşan.
#
# Durum dosyası: ~/.hermes/czip/nobetci.json  (sid -> son uyari + son deger)

import datetime as _dt
import json
import os
import re
import sqlite3
import subprocess
import sys

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
STATE_DB = os.path.join(HERMES_HOME, "state.db")
ENV_YOLU = os.path.join(HERMES_HOME, ".env")
CONFIG_YOLU = os.path.join(HERMES_HOME, "config.yaml")
DURUM_YOLU = os.path.join(HERMES_HOME, "czip", "nobetci.json")
TEKLIF_DIZIN = os.path.join(HERMES_HOME, "czip-teklifler")

ESİK_TOKEN = int(os.environ.get("CZIP_ESİK_TOKEN", "150000"))   # mutlak eşik (tahmini istek token)

# Eşik/kademe/oto ayarı artık tek yerden: ~/.hermes/czip/ayar.json
# (`czip ayar esik 50`, `czip ayar kademe 50,75,90`, `czip auto on`).
# Ayar dosyası okunamazsa eski davranışa düşülür — nöbetçi asla susmaz.
def _ayar():
    varsayilan = {"esik_oran": 0.30, "kademeler": [0.50, 0.75, 0.90],
                  "oto": False, "oto_pasif": False, "jev": True}
    try:
        sys.path.insert(0, os.path.expanduser(
            "~/007-HERMES/10-MCP-SERVERS/oturum-sikistirici"))
        import ayar as _a
        return _a.oku()
    except Exception:
        yol = os.path.join(HERMES_HOME, "czip", "ayar.json")
        try:
            with open(yol, encoding="utf-8") as f:
                varsayilan.update(json.load(f) or {})
        except Exception:
            pass
        env = os.environ.get("CZIP_ESİK_ORAN") or os.environ.get("CZIP_ESIK_ORAN")
        if env:
            try:
                varsayilan["esik_oran"] = float(env)
            except ValueError:
                pass
        return varsayilan


AYAR = _ayar()
ESİK_ORAN = float(AYAR.get("esik_oran", 0.30))                  # context_length'e oranlı eşik
KADEMELER = sorted(AYAR.get("kademeler") or [0.50, 0.75, 0.90])  # kademeli uyarı
OTO = bool(AYAR.get("oto"))                                      # sormadan paketle
SISTEM_PAYI = 25_000      # sistem promptu + araç tanımları tahmini
TEKRAR_SURESI_SA = 8      # aynı oturum için yeniden uyuma aralığı
ARTIS_GEREK = 25_000      # bu kadar büyümüşse erken yeniden uyar
PIES = "📦 Paketle + yeni sohbet"  # buton metni — hook bu birebir metni yakalar


def _simdi():
    return _dt.datetime.now(_dt.timezone.utc).timestamp()


def _env_deg():
    deger = {}
    try:
        with open(ENV_YOLU, encoding="utf-8") as f:
            for satir in f:
                satir = satir.strip()
                if satir and not satir.startswith("#") and "=" in satir:
                    a, b = satir.split("=", 1)
                    deger[a.strip()] = b.strip().strip('"').strip("'")
    except OSError:
        pass
    return deger


def _context_length(model):
    """config.yaml'dan model context_length — regex (pyyaml bağımlılığı yok)."""
    try:
        with open(CONFIG_YOLU, encoding="utf-8") as f:
            metin = f.read()
    except OSError:
        return 1_000_000
    if model:
        # "model_adi:" bloğundan sonraki ilk context_length
        m = re.search(re.escape(model) + r":[^\n]*(?:\n[ \t]+[^\n]+)*?\n[ \t]+context_length:\s*(\d+)",
                      metin)
        if m:
            return int(m.group(1))
    m = re.search(r"^  context_length:\s*(\d+)", metin, re.M)
    return int(m.group(1)) if m else 1_000_000


def _durum_oku():
    try:
        with open(DURUM_YOLU, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _durum_yaz(d):
    os.makedirs(os.path.dirname(DURUM_YOLU), exist_ok=True)
    tmp = DURUM_YOLU + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DURUM_YOLU)


def _kullanim(row):
    """(tahmini_istek, context_length) — anchor + sistem payı, mesaj tahminiyle max."""
    try:
        mc = json.loads(row["model_config"] or "{}")
    except Exception:
        mc = {}
    anchor = int((mc.get("_usage_anchor") or {}).get("prompt_tokens") or 0)
    ctx = _context_length(row["model"])
    tahmin_mb = 0
    # messages içerik baytı / 4 — kalın üst sınır
    return max(anchor + SISTEM_PAYI, tahmin_mb), ctx


def _aktifler(con):
    con.row_factory = sqlite3.Row
    # HAYALET KORUMASI (2026-09-21): mesajlari gece temizligince silinmis ama
    # sessions satiri ACIK kalmis oturumlar secilirse teklif uretilir, paketleme
    # 'eslesme yok'la patlar. Mesaji olmayan oturuma teklif YAZILMAZ.
    return con.execute(
        """SELECT s.id, s.model, s.model_config, s.chat_id, s.thread_id,
                  s.session_key, s.title, s.display_name, s.started_at
           FROM sessions s
           WHERE s.ended_at IS NULL
             AND LOWER(s.source) = 'telegram'
             AND s.chat_id IS NOT NULL
             AND s.started_at > ?
             AND (s.last_activity_at IS NULL OR s.last_activity_at > ?)
             AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
           ORDER BY s.started_at DESC""",
        (_simdi() - 7 * 86400, _simdi() - 2 * 86400)).fetchall()


def _telegram_gonder(token, chat_id, metin, thread_id=None, klavye=False):
    import urllib.request
    veri = {
        "chat_id": chat_id,
        "text": metin,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if thread_id:
        veri["message_thread_id"] = int(thread_id)
    if klavye:
        veri["reply_markup"] = json.dumps({
            "keyboard": [[{"text": PIES}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        })
    istek = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps(veri).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(istek, timeout=20) as yanit:
        v = json.loads(yanit.read().decode())
    if not v.get("ok"):
        raise RuntimeError("telegram: " + str(v)[:200])


def _teklif_yaz(chat_id, thread_id, sid, oran):
    gecilen = [k for k in KADEMELER if oran >= k]
    kademe_pct = int(round(gecilen[-1] * 100)) if gecilen else None
    """czip-tasi plugin'inin bekledigi teklif dosyasi — kullanicinin sonraki
    mesaji tiklanabilir 'sikistir/tasi' picker'ina donusur."""
    os.makedirs(TEKLIF_DIZIN, exist_ok=True)
    yol = os.path.join(TEKLIF_DIZIN, "%s-%s.json" % (chat_id, thread_id or "x"))
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sid": sid, "pct": int(oran * 100), "zaman": _simdi(),
                   "oto": OTO, "oto_pasif": bool(AYAR.get("oto_pasif")),
                   "jev": bool(AYAR.get("jev", True)),
                   "kademe": kademe_pct}, f,
                  ensure_ascii=False)
    os.replace(tmp, yol)


def _uyari_metni(sid, baslik, deger, ctx, oran):
    baslik = (baslik or sid)[:60].replace("_", " ")
    return (
        "⚠️ *Context şişiyor* — `{tip}`\n"
        "{baslik}\n"
        "Tahmini yük: ~{deger}k token / {ctx}k (%{oran})\n\n"
        "Alt butona bas: paketlerim, yeni oturum açarım, kaldığımız yerden "
        "paket ACILMADAN devam ederiz. (İstersen `/czip-tasi` da yazabilirsin.)"
    ).format(tip=sid, baslik=baslik,
             deger=round(deger / 1000), ctx=round(ctx / 1000), oran=int(oran * 100))


def main(argv):
    dry = "--dry" in argv
    test_sid = None
    if "--test" in argv:
        test_sid = argv[argv.index("--test") + 1] if len(argv) > argv.index("--test") + 1 else None

    con = sqlite3.connect("file:" + STATE_DB + "?mode=ro", uri=True, timeout=10)
    try:
        satirlar = _aktifler(con)
    finally:
        con.close()

    durum = _durum_oku()
    env = _env_deg()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    gonderen = 0
    satirlar_out = []

    for r in satirlar:
        sid = r["id"]
        if test_sid and test_sid not in sid:
            continue
        deger, ctx = _kullanim(r)
        esik = max(ESİK_TOKEN, ESİK_ORAN * ctx)
        oran = deger / ctx if ctx else 0
        if deger < esik and not test_sid:
            continue
        son = durum.get(sid) or {}
        gecen_sa = (_simdi() - float(son.get("zaman", 0))) / 3600.0
        if not test_sid:
            if gecen_sa < TEKRAR_SURESI_SA and deger - float(son.get("deger", 0)) < ARTIS_GEREK:
                continue  # yakın zamanda uyarıldı, yeter
        gecilen = [k for k in KADEMELER if oran >= k]
        kademe = gecilen[-1] if gecilen else None
        satirlar_out.append("%s ~%dk/%dk (%%%d) esik=%dk%s%s" % (
            sid, deger // 1000, ctx // 1000, oran * 100, int(esik) // 1000,
            ("  kademe=%%%d" % round(kademe * 100)) if kademe else "",
            "  OTO" if OTO else ""))
        if dry or not token:
            continue
        try:
            _telegram_gonder(token, r["chat_id"],
                             _uyari_metni(sid, r["title"] or r["display_name"], deger, ctx, oran),
                             r["thread_id"], klavye=True)
        except Exception as e:
            satirlar_out.append("GONDERME-HATASI %s: %s" % (sid, str(e)[:120]))
            # mesaj gitmese de teklif kalsin — kullanici mesaj atinca picker acilir
        try:
            _teklif_yaz(r["chat_id"], r["thread_id"], sid, oran)
        except OSError as e:
            satirlar_out.append("TEKLIF-YAZMA-HATASI %s: %s" % (sid, str(e)[:120]))
        durum[sid] = {"zaman": _simdi(), "deger": deger}
        gonderen += 1

    if durum:
        _durum_yaz(durum)
    # stdout: cron teslimi için kısa özet (sessiz kalması asıl hedef)
    if satirlar_out:
        print("context-nobetci: eşik aşan %d oturum, uyarı gönderilen %d" % (len(satirlar_out), gonderen))
        for s in satirlar_out:
            print("  " + s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
