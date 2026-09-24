# -*- coding: utf-8 -*-
"""yardim.py — czip-help: tum komutlar, gruplu, tanimlariyla (TEK kaynak).

CLI (`czip help`, `czip-help`, `czip help <konu>`), Hermes (`/czip_help`) ve
MCP (`czip_yardim`) ayni metni buradan alir; yeni komut tek yere eklenir.
Dil: CZIP_LANG=tr (Turkce) / en (varsayilan Ingilizce).
"""
from __future__ import annotations

import os

# (grup anahtari, TR baslik, EN baslik, [(komut, TR tanim, EN tanim), ...])
GRUPLAR = [
    ("paket", "PAKETLE & OKU", "PACK & READ", [
        ("czip pack <id|son|en-uzun|DOSYA|cc:son>",
         "Oturumu HKP1 paketine sikistirir, 6 haneli kisa ID verir. --eksiksiz: her bayt "
         "geri doner. --laya: buyuk arac ciktilarini yerel Laya'ya sordurur. --oto: ayni isi "
         "yapan oturumlari da ayni pakete alir.",
         "Compress a session into an HKP1 pack and get a 6-char id. --full: lossless. "
         "--laya: ask the local Laya gate about large tool outputs. --oto: pull in "
         "sessions doing the same job."),
        ("czip map <id|son>",
         "Ucuz giris (~1.5k token): istekler, arac histogrami, yon karti, son iletiler ve "
         "token tasarrufu satiri (COST). Paketi acmadan once hep bunu oku.",
         "Cheap entry (~1.5k tokens): requests, tool histogram, direction card, tail "
         "and a COST line. Always start here."),
        ("czip search <id|son> \"sorgu\"",
         "Tek paket icinde kelime arar; eslesen ileti numaralarini (i) verir.",
         "Search inside one pack; returns matching message numbers (i)."),
        ("czip around <id|son> <i> [--n=3]",
         "i numarali iletiyi her yandan n komsusuyla tam metin gosterir.",
         "Show message i with n neighbours each side, full text."),
        ("czip range <id|son> <a-b>",
         "Secili araligi birebir tam metin verir (en fazla 80 ileti).",
         "Exact full text of a range (max 80 messages)."),
        ("czip read <id|son>",
         "Tam indeks + son iletiler + durum. Buyuk paketlerde pahali; once map.",
         "Full index + tail + state. Expensive on big packs; prefer map."),
        ("czip yon <id|son>",
         "YON KARTI: hedef, bozulmamasi gereken kararlar, acik isler, son hata, dosyalar "
         "ve TEK sonraki adim. Devralinan isi surdurmeden once oku.",
         "DIRECTION CARD: goal, decisions not to break, open tasks, last error, files "
         "and ONE next step. Read before continuing inherited work."),
        ("czip list", "Kayitli paketler: id | tarih | baslik.",
         "Registered packs: id | date | title."),
    ]),
    ("hafiza", "HAFIZA (RAG)", "MEMORY (RAG)", [
        ("czip hatirla \"istek\"",
         "Bu istekle ilgili gecmis isi bulur. Alaka kapisi: kelimelerin en az yarisi ayni "
         "iletide gecmeli; alakasizsa bos doner.",
         "Find past work related to a request. Relevance gate: at least half the "
         "words in one message; returns nothing when irrelevant."),
        ("czip asearch \"sorgu\"",
         "TUM paketlerde tam ifade arar (uzun sureli hafiza).",
         "Exact-phrase search across ALL packs (long-term memory)."),
        ("czip gunluk [--n=10]",
         "\"Ne yaptim?\": her paket icin tek satir (tarih, proje, ID, sonraki adim).",
         "\"What did I do?\": one line per pack (date, project, id, next step)."),
        ("czip brifing [--cwd=DIZIN]",
         "Acilis brifingi: bu projede son isler + son paketin yon karti.",
         "Session brief: recent work in this project + last direction card."),
        ("czip index [--full]",
         "Arama deposunu gunceller (paketlemede zaten kendiliginden yapilir).",
         "Refresh the search store (already done automatically after packing)."),
    ]),
    ("auto", "OTOPILOT & ESIKLER", "AUTOPILOT & THRESHOLDS", [
        ("czip auto64 | auto128 | auto32 | auto256 | autoN",
         "Baglam her N bin token buyudukce oturumu kendiliginden, kayipsiz paketler "
         "(64k, 128k, 192k ...). /compact sayaci sifirlar. Hermes'i de yonetir.",
         "Auto-pack losslessly every N thousand tokens of context (64k, 128k, 192k ...). "
         "/compact resets the counter. Drives Hermes too."),
        ("czip auto off", "Adim modunu kapatir, yuzde kademelerine doner.",
         "Turn step mode off, back to percentage tiers."),
        ("czip auto", "Aktif modu ve hazir secenekleri gosterir.",
         "Show the active mode and presets."),
        ("czip settings", "Tum ayarlari gosterir (~/.hermes/czip/ayar.json).",
         "Show all settings (~/.hermes/czip/ayar.json)."),
        ("czip settings esik 50 | kademe 50,75,90",
         "Yuzde esigi / kademeler (adim modu kapaliyken). Kisa yol: czip 50",
         "Percentage threshold / tiers (when step mode is off). Shortcut: czip 50"),
        ("czip settings kalan 70000 | mutlak 64000",
         "Kalan yer bu kadar token'a inince / kullanim bu mutlak degere ulasinca tetikle.",
         "Fire when this much room is left / at this absolute usage."),
        ("czip settings ctx 1000000",
         "Claude baglam penceresi (varsayilan 200k). 1M modelde mutlaka ayarla.",
         "Claude context window (default 200k). Set it for 1M models."),
        ("czip settings koruma|hatirlatma|brifing|haftalik_temizlik on|off",
         "Otopilot parcalarini tek tek acar/kapatir.",
         "Switch autopilot parts on/off individually."),
        ("czip settings oto on|off",
         "Hermes: esikte sormadan paketle (on) ya da teklif butonu goster (off).",
         "Hermes: pack without asking at threshold (on) or show the offer button (off)."),
    ]),
    ("temizlik", "BIRLESTIRME & TEMIZLIK", "MERGE & CLEANUP", [
        ("czip merge [oto|<id1> <id2>] [--laya] [--gun=7]",
         "Ayni isi yapan oturumlari tek pakete alir. Argumansiz: yalniz aday taramasi.",
         "Merge sessions doing the same job into one pack. No args: scan only."),
        ("czip undo [<dosya>]", "Birlestirmede pasife alinan oturumlari geri acar.",
         "Reopen sessions deactivated by a merge."),
        ("czip temizle", "Haftalik temizlik PLANI — hicbir seye dokunmaz.",
         "Weekly cleanup PLAN — touches nothing."),
        ("czip temizle --uygula (--apply)",
         "Gereksiz yiginlari COPE tasir: eski ayni-oturum paketleri (ID yeni pakete "
         "yonlenir), birebir kopyalar, hayalet paketler, 14 gunluk .bak'lar, 2MB+ loglar. "
         "Cop 30 gun sonra bosalir. Otopilot bunu 7 gunde bir kendisi yapar.",
         "Move junk to TRASH: superseded packs (id redirected), exact copies, ghost "
         "packs, 14-day .bak files, 2MB+ logs. Trash empties after 30 days. The "
         "autopilot runs this every 7 days."),
        ("czip temizle geri (undo)", "Son temizligi geri alir.", "Undo the last cleanup."),
    ]),
    ("claude", "CLAUDE CODE / DESKTOP", "CLAUDE CODE / DESKTOP", [
        ("czip cc [--n=10]", "Son Claude Code/Desktop oturumlarini listeler.",
         "List recent Claude Code/Desktop sessions."),
        ("czip pack cc:son | cc:<uuid>", "Bir Claude oturumunu paketler.",
         "Pack a Claude session."),
        ("czip merge <hermes-id> cc:<uuid>", "Hermes ve Claude oturumunu tek pakette birlestirir.",
         "Merge a Hermes and a Claude session into one pack."),
        ("./install.sh --claude",
         "Claude Desktop'a MCP + Claude Code'a beceri ve otopilot hook'larini kurar "
         "(once yedek alir; pip gerekmez).",
         "Install MCP into Claude Desktop + skill and autopilot hooks into Claude Code "
         "(backs up first; no pip needed)."),
        ("czip hook <Olay>", "Claude Code hook girisi (elle calistirilmaz).",
         "Claude Code hook entry point (not for manual use)."),
    ]),
    ("karar", "KARAR KAPISI (LAYA)", "DECISION GATE (LAYA)", [
        ("--laya (eski: --jev)",
         "Buyuk arac ciktilarini yerel Laya'ya sorar: olu olan paketten cikar, gerekli olan "
         "aynen kalir. Laya yoksa sessizce atlanir.",
         "Ask the local Laya model about large tool outputs: dead ones leave the pack, "
         "needed ones stay verbatim. Skipped silently without Laya."),
        ("czip settings motor laya|jev",
         "Karar motoru: laya = yerel (varsayilan), jev = bulut.",
         "Gate engine: laya = local (default), jev = cloud."),
        ("czip settings bulut on|off",
         "Laya cokerse bulut Jev'e dus. Varsayilan KAPALI (ornek veri disari gider).",
         "Fall back to cloud Jev if Laya fails. Default OFF (samples leave the machine)."),
    ]),
    ("hermes", "HERMES KOMUTLARI", "HERMES COMMANDS", [
        ("/czip [son|aktif|<id>]", "Oturumu paketler, sonraki adimi gosterir.",
         "Pack the session and show the next step."),
        ("/czipex (/cunzip) <paket|ID> [a-b]", "Paketi acmadan okur.", "Read a pack without unpacking."),
        ("/czipmerge [bak|oto|<id1> <id2>]", "Ayni isi yapan oturumlari birlestirir.",
         "Merge sessions doing the same job."),
        ("/czipauto 64 | 128 | off", "czip auto64/auto128 ile ayni: sormadan adimli paketleme.",
         "Same as czip auto64/auto128: step auto-pack without asking."),
        ("/czip_help [konu]", "Bu yardim.", "This help."),
    ]),
    ("mesaj", "OTOPILOT MESAJLARI (AI'nin gordugu)", "AUTOPILOT MESSAGES (what the AI sees)", [
        ("[CZIP HAFIZA] + YON KARTI",
         "Oturum acilisi: bu projede son isler ve devam noktasi. Kararlari bozma, SONRAKI "
         "ADIM'dan basla, once diskte dogrula.",
         "Session start: recent work and where to continue. Keep the decisions, start "
         "from NEXT STEP, verify on disk first."),
        ("[CZIP BAGLAM KORUMA]",
         "Yuzde esigi gecildi, oturum paketlendi. Baglami sisirme; %90'da /compact oner.",
         "Percentage tier crossed, session packed. Keep context lean; suggest /compact at 90%."),
        ("[CZIP-AUTO64]", "Adim modunda 64k adimi gecildi, oturum paketlendi.",
         "Step mode crossed a 64k step, session packed."),
        ("[CZIP HATIRLATMA]", "Bu istekle ilgili gecmis is; gerekirse czip range ile oku.",
         "Past work related to this request; read it with czip range if needed."),
        ("[CZIP BAGLANTISI]",
         "Compaction sonrasi: ozetin dusurdugu her sey adi gecen pakette — tahmin etme, oku.",
         "After compaction: whatever the summary dropped is in the named pack — look it up."),
    ]),
]

KONU_TAKMA = {"pack": "paket", "read": "paket", "memory": "hafiza", "rag": "hafiza",
              "oto": "auto", "esik": "auto", "threshold": "auto", "settings": "auto",
              "ayar": "auto", "clean": "temizlik", "cleanup": "temizlik", "merge": "temizlik",
              "birlestir": "temizlik", "cc": "claude", "desktop": "claude", "laya": "karar",
              "gate": "karar", "jev": "karar", "slash": "hermes", "mesajlar": "mesaj",
              "messages": "mesaj"}


def metin(konu=None, dil=None, genislik=100):
    """czip-help metni. konu: grup anahtari/takma adi ya da komut parcasi (ornek 'auto64')."""
    import textwrap
    tr = (dil or os.environ.get("CZIP_LANG") or "en").lower().startswith("tr")
    k = (konu or "").strip().lower().lstrip("-/")
    k = KONU_TAKMA.get(k, k)
    gruplar = [g for g in GRUPLAR if not k or g[0] == k]
    if k and not gruplar:  # komut parcasiyla ara
        gruplar = [(g[0], g[1], g[2], [c for c in g[3] if k in c[0].lower()])
                   for g in GRUPLAR]
        gruplar = [g for g in gruplar if g[3]]
    if not gruplar:
        return ("Konu bulunamadi: %s. Konular: " if tr else "No such topic: %s. Topics: ") % konu \
            + ", ".join(g[0] for g in GRUPLAR)
    s = ["czip-help — " + ("tum komutlar ve tanimlari" if tr else "every command, explained")]
    for anahtar, tr_b, en_b, komutlar in gruplar:
        s.append("")
        s.append("== %s  (czip help %s)" % (tr_b if tr else en_b, anahtar))
        for komut, tr_t, en_t in komutlar:
            s.append("  " + komut)
            s.extend(textwrap.wrap(tr_t if tr else en_t, genislik - 6,
                                   initial_indent="      ", subsequent_indent="      "))
    if not k:
        s.append("")
        s.append(("Tek konu: " if tr else "One topic: ")
                 + "czip help <%s>" % "|".join(g[0] for g in GRUPLAR)
                 + ("" if tr else "   · Turkish: CZIP_LANG=tr"))
    return "\n".join(s)


def komutlar():
    """Tum komut satirlari (test ve tamamlama icin)."""
    return [c[0] for g in GRUPLAR for c in g[3]]
