---
description: Tüm czip arşivinde ara — "bunu nerede yapmıştım?" sorusunun cevabı, ms mertebesinde
argument-hint: "\"<sorgu>\"   |   <ID> \"<sorgu>\" (tek pakette ara)   |   index"
allowed-tools: Bash
---

`$ARGUMENTS` `index` ise depoyu tazele, sonra dur:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" index
```

`$ARGUMENTS` 6 haneli bir paket ID'si ile başlıyorsa **o paketin içinde** ara:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  search $ARGUMENTS
```

Aksi halde **tüm arşivde** ara:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  asearch $ARGUMENTS
```

Çıktıyı **olduğu gibi** göster, sonra tek satırda ne bulduğunu özetle.

Okuma kuralı: isabet satırları zaten bağlam veriyor. Tam metin gerekiyorsa
**yalnız o aralığı** çek — `czip range <ID> <bas-bit>`. Paketin tamamını asla
yükleme; `czip read` bile 90k token'a çıkabilir, `czip map` ~1.5k'dır.

Kapsam notu: yalnız **paketlenmiş** oturumlar aranır. Aranan şey çıkmıyorsa
oturum henüz paketlenmemiş olabilir — `/czip <session_id>` ile paketle, sonra
`/czip_search index` ile depoya al.
