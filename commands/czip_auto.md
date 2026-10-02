---
description: Otomatik czip modunu aç/kapat — eşiği geçince sormadan paketler (Jev kararlı)
argument-hint: "[on|off]"
allowed-tools: Bash
---

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" \
  auto ${ARGUMENTS:-on}
```

Ardından güncel ayarı göster:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
python3 \
  "${CZIP_HKP:-$HOME/czip/hkp.py}" ayar
```

**Açıkken**: context kullanımı eşiği geçince kararı kendi verir, oturumu paketler,
aynı işi yapan oturumları da (Jev onayıyla) aynı pakete alır.
**Kapalıyken**: tek tıklık yönlendirme menüsü çıkar, kararı sen verirsin.

Eşiği değiştirmek için `/czip_esik 50` veya `/czip_esik 50,75,90`.
