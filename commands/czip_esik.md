---
description: czip eşiğini ayarla — context'in yüzde kaçında devreye girsin
argument-hint: "50   |   50,75,90 (kademeli)"
allowed-tools: Bash
---

`$ARGUMENTS` tek sayıysa eşik, virgüllüyse kademe listesi olarak ayarla:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PY=python3
HK="${CZIP_HKP:-$HOME/czip/hkp.py}"
case "$ARGUMENTS" in
  *,*) HERMES_HOME=$HERMES_HOME $PY $HK ayar kademe "$ARGUMENTS" ;;
  "")  HERMES_HOME=$HERMES_HOME $PY $HK ayar ;;
  *)   HERMES_HOME=$HERMES_HOME $PY $HK ayar esik "$ARGUMENTS" ;;
esac
HERMES_HOME=$HERMES_HOME $PY $HK ayar
```

Eşik, modelin context penceresine oranlıdır — 256k'lık modelde `50` → ~128k.
Kademeler her biri oturum başına bir kez tetiklenir.
