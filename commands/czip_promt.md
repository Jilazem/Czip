---
description: Ham görevi düzgün bir prompta çevirir, onayını alır, czip deposuna yazar ve işe başlar
argument-hint: "<ham görev metni>   |   oku <kid|son>   |   listele [--tip=promt]"
allowed-tools: Bash, Read, Grep, Glob
---

`$ARGUMENTS` ilk kelimesi `oku` veya `listele` ise **depo komutudur**, aşağıdaki
1–4 adımı atla ve doğrudan çalıştır:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  gorev $ARGUMENTS
```

Aksi halde `$ARGUMENTS` ham bir görev tarifidir. Şunu yap:

**1. Prompta çevir.** Ham tarifi tek seferde çalıştırılabilir bir prompta dönüştür:
rol/bağlam, somut görev, girdi dosyaları veya yollar, beklenen çıktı biçimi,
kapsam dışı bırakılanlar, ve varsa kabul ölçütü. Kullanıcının kendi kelimelerini
koru — "düzeltmek" adına görevi değiştirme. Belirsiz kalan yer varsa **uydurma**;
prompta `[BELİRSİZ: ...]` olarak yaz, 2. adımda sor.

**2. Göster ve sor.** Promtu kod bloğu içinde göster, tek cümlelik "bunu mu
istiyorsun?" sorusuyla bitir. Belirsizlikler varsa `AskUserQuestion` ile sor.
**Onay gelmeden 3. adıma geçme.**

**3. Onay gelince kaydet.** Onaylanan metni aynen stdin'den geçir:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  gorev kaydet --tip=promt --baslik="<kısa başlık>" --ham="<ham istek>" <<'PROMT'
<onaylanmış prompt metni>
PROMT
```

**4. Hemen başla.** Kaydın döndürdüğü `kid`'i tek satırda bildir, sonra
onaylanmış promtu kendi görevin gibi uygulamaya başla. Promtu tekrar özetleme —
tur-tur tarif token'ı işin kendisinden çalar; zaten depoda duruyor.

Kayıt küçük bir `.hkp` paketidir, `czip index` onu tarar: sonraki oturumda
`/czip_promt oku <kid>` veya `czip asearch "<konu>"` ile aynı prompt bedavaya
geri gelir — görevi baştan tarif etmeye gerek kalmaz.
