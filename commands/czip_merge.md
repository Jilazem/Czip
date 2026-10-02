---
description: Aynı işi yapan oturumları tek pakete birleştir — kararı Jev verir
argument-hint: "[oto|<id1> <id2> ...] [--jev] [--gun=7]"
allowed-tools: Bash
---

Oturum birleştiriciyi çalıştır, çıktıyı kullanıcıya **olduğu gibi** göster.

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  birlestir $ARGUMENTS
```

Davranış:
- **Argümansız** → salt-okunur aday taraması, hiçbir şey yazmaz. Önce bunu çalıştır.
- `<id1> <id2>` → o oturumları birleştirip tek pakete sıkıştırır.
- `oto` → en güçlü grubu otomatik birleştirir.
- `--jev` → "bu oturumlar gerçekten aynı iş mi?" sorusunu Jev karar kapısına sorar.
  **Kullanmaya değer:** yerel benzerlik iki ayrı dava dosyasını 0.75 ile birleştirmeye
  kalkmıştı, Jev 0.13 verip reddetti.
- Kaynak oturumlara **dokunmaz** (`state.db` salt-okunur). Oturum sınırları pakette
  görünür ayraçla işaretlenir, oturumlar arası birebir aynı iletiler bir kez tutulur.
