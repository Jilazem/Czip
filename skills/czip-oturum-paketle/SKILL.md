---
name: czip-oturum-paketle
description: "Use when: 'oturumu paketle', 'czip'. HKP1 paketle/oku."
---

# czip — oturum paketle / paket açmadan oku

Kullanıcı bir oturumu yeni oturuma taşımak istediğinde (veya "czip" derse)
terminal üzerinden çalıştır. Asla paketin tamamını context'e yükleme.

## Komutlar (terminal aracıyla, birebir)

1. Paketle:  `czip paketle <session_id|son|en-uzun> [--eksiksiz]`
2. Oku:      `czip oku <paket.hkp|son>`  (INDEKS + son 6 ilet + durum, ~40KB)
3. Aralık:   `czip aralik <paket.hkp|son> <bas-bit>`

## Kurallar
- INDEKS'ten ilgili mesaj numaralarını bul, yalnız o aralıkları oku.
- "kaldığın yerden devam" = son iletler bölümü zaten tam verilir.
- Kayıpsız taşıma gerekiyorsa `--eksiksiz` kullan.
