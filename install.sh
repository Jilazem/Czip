#!/bin/bash
# czip kurulumu — terminal girisi + Hermes plugin + skill + MCP
#   ./install.sh            -> Hermes (varsa) + terminal
#   ./install.sh --claude   -> ayrica Claude Desktop + Claude Code (MCP + skill + otopilot hook'lari)
set -e
KOK="$(cd "$(dirname "$0")" && pwd)"
HE="${HERMES_HOME:-$HOME/.hermes}"
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "python3 bulunamadi"; exit 1; }

# 1) terminal girisi (yuklenen komut repo motorunu gosterir)
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\nexec "%s" "%s/hkp.py" "$@"\n' "$PY" "$KOK" > "$HOME/.local/bin/czip"
chmod +x "$HOME/.local/bin/czip" "$KOK/czip"

# 2) Hermes plugin + skill + MCP (hermes varsa)
if [ -d "$HE" ] || command -v hermes >/dev/null 2>&1; then
  mkdir -p "$HE/plugins/czip" "$HE/skills/czip-oturum-paketle"
  cp "$KOK/plugin/__init__.py" "$KOK/plugin/plugin.yaml" "$HE/plugins/czip/"
  cp "$KOK/skills/czip-oturum-paketle/SKILL.md" "$HE/skills/czip-oturum-paketle/"
fi
if command -v hermes >/dev/null 2>&1; then
  yes | hermes mcp add oturum-sikistirici --command "$PY" --args "$KOK/mcp_server.py" || true
  yes | hermes plugins enable czip --allow-tool-override || true
  echo "OK Hermes — yeni oturum ac: /czip, /cunzip, czip komutu ve skill hazir."
fi

# 3) Claude Desktop + Claude Code (istege bagli)
if [ "$1" = "--claude" ]; then
  "$PY" "$KOK/scripts/claude_kur.py" --python "$PY" --hooks
  mkdir -p "$HOME/.claude/skills/czip-session-pack"
  cp "$KOK/skills/czip-session-pack/SKILL.md" "$HOME/.claude/skills/czip-session-pack/"
  if command -v claude >/dev/null 2>&1; then
    claude mcp add --scope user czip -- "$PY" "$KOK/mcp_server.py" || true
  fi
  echo "OK Claude — Desktop'u yeniden baslat. Otopilot acik: brifing + baglam koruma +"
  echo "   RAG hatirlatma + compaction oncesi paket + haftalik temizlik (czip ayar ile kapat)"
fi
echo "(~/.local/bin PATH'te olmali; ornek: export PATH=\"\$HOME/.local/bin:\$PATH\")"
