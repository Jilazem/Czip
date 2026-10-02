---
description: Oturumu .hkp paketine sıkıştır — aynı işi yapan oturumları otomatik birleştirir
argument-hint: "[son|aktif|<id>|<dosya>] [--oto] [--oto-pasif] [--jev] [--eksiksiz]"
allowed-tools: Bash
---

Oturum sıkıştırıcıyı çalıştır, çıktıyı kullanıcıya **olduğu gibi** göster.

Kullanıcı sadece `/czip` dediyse varsayılan **`son --jev --oto`** kullan —
yani aynı işi yapan oturumları da otomatik aynı pakete al.

**Konuşmaya bir dosya eklenmişse veya `$ARGUMENTS` bir dosya yoluysa**, o dosyayı
paketle — oturum kimliği gibi arama. Motor JSON, JSONL, düz metin, mesaj listesi
ve Hermes export bloğunu tanır; başlığı JSON içindeki `title` alanından alır.
Yolda boşluk varsa tırnak içine al.

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  paketle ${ARGUMENTS:-son --jev --oto}
```

Bayraklar:
- `--oto` → aynı işi yapan oturumları tespit edip **aynı pakete alır** (kararı Jev verir)
- `--oto-pasif` → ayrıca kaynak oturumları kapatır + arşivler (ileti silinmez; geri al: `czip gerial <dosya>`)
- `--jev` → Jev karar kapısı. **Kullan:** yerel benzerlik iki ayrı dava dosyasını 0,75 ile
  birleştirmeye kalkmıştı, Jev 0,13 verip reddetti.
- `--eksiksiz` → kayıpsız mod (kırpma, Jev budaması ve tekrar ayıklama devre dışı)

Paketi sonra **`/czip_ex`** ile RAG mantığıyla oku — tam dökümü asla yükleme.
Kaynak oturumlara dokunulmaz (`state.db` salt-okunur; pasife alma hariç).
