---
name: czip-oturum-paketle
description: "Use when: 'oturumu paketle', 'czip'. HKP1 paketle/oku."
---

# czip — oturum paketle / paket açmadan oku

Kullanıcı bir oturumu yeni oturuma taşımak istediğinde (veya "czip" derse)
terminal üzerinden çalıştır. Asla paketin tamamını context'e yükleme.

## Komutlar (terminal aracıyla, birebir)

1. Paketle (aktif/istenen oturum):
   `czip paketle <session_id|son|en-uzun> [--eksiksiz]`
   - kullanıcı id vermediyse: `hermes sessions list` benzeri ile bul veya
     `son` kullan (son aktif oturum = genelde bu konuşma).
   - çıktıda 6 haneli KISA ID üretir (kayit.json'a yazılır) — dosya yolu yerine ID kullan.
2. Yeni oturumda oku (indeks + son 6 ilet + durum, ~40KB):
   `czip oku <id|paket.hkp|son>`
3. Detay gereken aralık (tam metin, jetonler çözülmüş):
   `czip aralik <id|son> <bas-bit>`
4. Paket İÇİDE RAG araması (paket açılmadan, eşleşen mesaj i numaraları + çevre):
   `czip ara <id|son> "sorgu"`  → bulunan i için `czip aralik <id> i` ile tam metin al.
5. Kayıtlı paketler: `czip listele` (id | tarih | başlık).

Paketler: `~/.hermes/session-packs/*.hkp`

## Tek tık buton akışı (czip-tasi plugin + context-nobetci cron)
- `context-nobetci.py` cron'u (her 15 dk, --no-agent) aktif oturumların
  `model_config._usage_anchor.prompt_tokens` değerini izler; eşik =
  max(150k, %30·context_length) (env: CZIP_ESİK_TOKEN / CZIP_ESİK_ORAN).
- Eşiği aşan sohbetin Telegram'ına uyarı + `~/.hermes/czip-teklifler/<chat>-<thread>.json`
  teklif dosyası yazar (durum: ~/.hermes/czip/nobetci.json, 8h uyku).
- Kullanıcı o sohbete SONRAKİ mesajı atınca czip-tasi plugin'i mesajı yutar ve
  TEK TIK picker açar: 📦 akilli / 🧊 eksiksiz / ❌ iptal.
- Tıkta: paketle → `store.reset_session` (resmî /new yolu) → yeni oturumun İLK
  mesajına `[CZIP BAGLANTISI]` talimatı enjekte edilir (pre_llm_call, tek seferlik).
- Plugin gateway RESTART'ıyla yüklenir (allowlist: plugins.enabled).

## Karar kapısı: Laya (23.09.2026 — yerel, buluta veri gitmez)
Jev bulut kapısı 21.09'da gizlilik gerekçesiyle kapatıldı. Yerine YEREL motor:
- `--laya` (eski ad `--jev`) büyük araç çıktılarını yerel Laya'ya sorar
  (işçi: `laya_kapi.py`, laya-mlx venv'i: `CZIP_LAYA_PY`).
- Eşikler: silme 0.15 (Jev'in 0.30'undan korumacı), tutma 0.55.
  Hata içeren çıktılar sorgulanmadan korunur.
- Türkçe içerik önce node1 (Qwen) ile EN'e çevrilir; kapatmak: `CZIP_LAYA_CEVIRI=0`.
- Motor: `czip ayar motor laya|jev`. Laya çalışmazsa kapı atlanır; bulut
  Jev'e düşüş YALNIZ `czip ayar bulut on` ile (varsayılan kapalı, KVKK).

## Claude Code / Claude Desktop oturumları
- Liste: `czip cc` · paketle: `czip paketle cc:son` / `cc:<uuid>`
- Hermes + Claude tek pakette: `czip birlestir <hermes-id> cc:<uuid>`
- Çevre okuma: `czip cevre <id> <i>` (i±3 tam metin)

## Otopilot mesajları (Claude Code hook'ları) — nasıl davranılır
- `[CZIP HAFIZA]` / `YON KARTI`: listelenen **kararları** bozma (sormadan geri
  alma), **SONRAKI ADIM**'dan başla, önce dosyaları/branch'i diskte doğrula.
- `[CZIP BAGLAM KORUMA]`: oturum zaten paketlendi. Bağlamı şişirme: dosyanın
  tamamı yerine grep / offset+limit, uzun çıktıda head/tail, yeniden okumak
  yerine `czip ara <id> "..."`. %90'da alt iş bitince kullanıcıya /compact öner.
- `[CZIP HATIRLATMA]`: bu istekle ilgili geçmiş iş; önemliyse tekrar yapmadan
  önce `czip aralik <id> <i>` ile oku.
- `[CZIP BAGLANTISI]` (sıkıştırma sonrası): özette eksik kalan pakette — tahmin etme.
- Haftalık temizlik kendiliğinden çalışır: `czip temizle` (plan), `czip temizle geri`.

## Kurallar
- `czip oku` çıktısındaki INDEKS'ten ilgili mesaj numaralarını bul,
  yalnız o aralıkları `czip aralik` ile oku — tüm paketi asla okuma.
- "kaldığın yerden devam" = son iletler bölümü zaten tam verilir.
- Kayıpsız taşıma gerekiyorsa `--eksiksiz` kullan (8.4x yerine ~7x).
- Aynı işi MCP araçları ile de yapabilirsin: oturum-sikistirici MCP'sinin
  `oturum_sikistir`, `kilavuz`, `mesajlar` araçları (varsa tercih sırası farksız).
