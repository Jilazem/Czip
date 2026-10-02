---
description: Paketi çıkart/oku — RAG yöntemiyle, tam dökümü asla yüklemeden
argument-hint: "<paket.hkp|ID|son> [ara <sorgu> | <bas-bit> | tam]"
allowed-tools: Bash
---

Sıkıştırılmış oturumu **RAG mantığıyla** oku. Tam döküm ASLA yükleme.

`$ARGUMENTS` boşsa veya sadece bir ID ise → **harita** al (~2k token):

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  harita ${ARGUMENTS:-son}
```

Sonra **ihtiyaç duyduğun kadarını** çek:
- Anahtar kelimeyle: `hkp.py ara <id> "<sorgu>"` → isabet listesi + mesaj numaraları (~100 token)
- O numaraları tam oku: `hkp.py aralik <id> <bas-bit>`

`tam` dendiyse (ve yalnızca o zaman) eski davranış: `hkp.py oku <id>`.

**Neden böyle** (3421 iletlik gerçek oturumda ölçüldü):

| Yol | Token |
|---|---|
| `oku` (tam indeks) | 93.417 |
| `harita` + hedefli `ara`/`aralik` | ~2.100 → **44× ucuz** |

Paket dosyasının kendisi context'e **hiç girmez** — maliyet sadece bu çıktıdır.
