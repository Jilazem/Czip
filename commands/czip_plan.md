---
description: Ham görevi uygulama planına çevirir, onayını alır, czip deposuna yazar ve uygular
argument-hint: "<ham görev metni>   |   oku <kid|son>   |   listele [--tip=plan]"
allowed-tools: Bash, Read, Grep, Glob
---

`$ARGUMENTS` ilk kelimesi `oku` veya `listele` ise **depo komutudur**, aşağıdaki
1–4 adımı atla ve doğrudan çalıştır (`listele` için `--tip=plan` ekle):

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  gorev $ARGUMENTS
```

Aksi halde `$ARGUMENTS` ham bir görev tarifidir. `/czip_promt` görevi **prompta**
çevirir; bu komut **plana** çevirir — yani işi yapmadan önce adımları çıkarır.

**1. Keşfet, sonra planla.** Gerekli dosyaları oku (Read/Grep/Glob). Plan somut
olsun: numaralı adımlar, her adımda dokunulacak dosya yolu, adımın bitti sayılma
ölçütü, riskli/geri alınamaz adımların işareti, ve kapsam dışı bırakılanlar.
Belirsiz kalan yeri uydurma — `[BELİRSİZ: ...]` diye yaz.

**2. Göster ve sor.** Planı göster, "böyle mi ilerleyelim?" diye sor. Belirsizlik
varsa `AskUserQuestion` kullan. **Onay gelmeden uygulamaya geçme.**

**3. Onay gelince kaydet.**

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  gorev kaydet --tip=plan --baslik="<kısa başlık>" --ham="<ham istek>" <<'PLAN'
<onaylanmış plan metni>
PLAN
```

**4. Uygula.** `kid`'i tek satırda bildir, sonra planı adım adım uygula.
Oturum dolar da paketlenirse plan kaybolmaz: yeni oturumda
`/czip_plan oku <kid>` planı aynen geri getirir, kalınan yerden devam edilir.
