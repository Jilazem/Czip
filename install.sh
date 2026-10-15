#!/bin/bash
# Install portable CLI + Hermes integration without overwriting a shared launcher.
set -euo pipefail
KOK="$(cd "$(dirname "$0")" && pwd)"
HE="${HERMES_HOME:-$HOME/.hermes}"
PY="${CZIP_PYTHON:-$(command -v python3 || command -v python)}"
[ -n "$PY" ] || { echo "python3 required / gerekli"; exit 1; }
mkdir -p "$HOME/.local/bin"
TARGET="$HOME/.local/bin/czip"
# Existing launchers may route shared front/search services. Preserve them.
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  TARGET="$HOME/.local/bin/czip-local"
fi
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  cp -p "$TARGET" "$TARGET.backup-$(date +%s)-$$"
fi
printf '#!/bin/sh\nexec "%s" "%s/czip_cli.py" "$@"\n' "$PY" "$KOK" > "$TARGET"
chmod +x "$TARGET"
mkdir -p "$HE/plugins/czip" "$HE/skills/czip-oturum-paketle"
for FILE in __init__.py plugin.yaml engine-path.txt; do
  if [ -e "$HE/plugins/czip/$FILE" ]; then
    cp -p "$HE/plugins/czip/$FILE" "$HE/plugins/czip/$FILE.backup-$(date +%s)-$$"
  fi
done
cp "$KOK/plugin/__init__.py" "$KOK/plugin/plugin.yaml" "$HE/plugins/czip/"
printf '%s/hkp.py\n' "$KOK" > "$HE/plugins/czip/engine-path.txt"
if [ -e "$HE/skills/czip-oturum-paketle/SKILL.md" ]; then
  cp -p "$HE/skills/czip-oturum-paketle/SKILL.md" "$HE/skills/czip-oturum-paketle/SKILL.md.backup-$(date +%s)-$$"
fi
cp "$KOK/skills/czip-oturum-paketle/SKILL.md" "$HE/skills/czip-oturum-paketle/"
# CLI can work without Hermes or MCP. Enabling integration is explicit.
if [ "${1:-}" = "--enable-hermes" ]; then
  hermes mcp add oturum-sikistirici --command "$PY" --args "$KOK/mcp_server.py"
  hermes plugins enable czip --allow-tool-override
fi
printf 'Installed / Kuruldu: %s\n' "$TARGET"
echo 'Hermes: enable czip, then reload plugins or open a new process / eklentiyi etkinleştirip yeniden yükle.'
echo 'MCP requires the optional mcp dependency / MCP için isteğe bağlı mcp bağımlılığı gerekir.'
