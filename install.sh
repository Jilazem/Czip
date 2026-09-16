#!/bin/bash
# czip kurulumu — terminal girisi + plugin + skill + MCP
set -e
KOK="$(cd "$(dirname "$0")" && pwd)"
HE="${HERMES_HOME:-$HOME/.hermes}"
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "python3 bulunamadi"; exit 1; }

# 1) terminal girisi (yuklenen komut repo motorunu gosterir)
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\nexec "%s" "%s/hkp.py" "$@"\n' "$PY" "$KOK" > "$HOME/.local/bin/czip"
chmod +x "$HOME/.local/bin/czip" "$KOK/czip"

# 2) plugin
mkdir -p "$HE/plugins/czip"
cp "$KOK/plugin/__init__.py" "$KOK/plugin/plugin.yaml" "$HE/plugins/czip/"

# 3) skill
mkdir -p "$HE/skills/czip-oturum-paketle"
cp "$KOK/skills/czip-oturum-paketle/SKILL.md" "$HE/skills/czip-oturum-paketle/"

# 4) MCP + plugin etkinlestirme (hermes varsa)
if command -v hermes >/dev/null 2>&1; then
  yes | hermes mcp add oturum-sikistirici --command "$PY" --args "$KOK/mcp_server.py" || true
  yes | hermes plugins enable czip --allow-tool-override || true
fi
echo "OK — yeni Hermes oturumu ac: /czip, /cunzip, czip komutu ve skill hazir."
echo "(~/.local/bin PATH'te olmali; ornek: export PATH=\"\$HOME/.local/bin:\$PATH\")"
