#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude_kur.py — czip MCP sunucusunu Claude Desktop'a tanitir.

claude_desktop_config.json'a 'czip' girdisini EKLER (diger sunuculara
dokunmaz), once .bak-czip yedegi alir. Tekrar calistirmak zararsizdir.
Kullanim: python3 scripts/claude_kur.py [--python /yol/python3] [--hooks] [--kaldir]

--hooks: czip otopilot hook'larini ~/.claude/settings.json'a da ekler
(SessionStart / UserPromptSubmit / PreCompact). Onceki czip girdileri
degistirilir, baska hook'lara dokunulmaz, once .bak-czip yedegi alinir.
"""
import json
import os
import shutil
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_yolu():
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Claude/claude_desktop_config.json")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "Claude",
                            "claude_desktop_config.json")
    return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")


OLAYLAR = {"SessionStart": ("startup|clear|compact", 30),
           "UserPromptSubmit": (None, 60), "PreCompact": (None, 120)}


def hooks_kur(py, kaldir=False):
    yol = os.environ.get("CLAUDE_SETTINGS") or os.path.expanduser("~/.claude/settings.json")
    veri = {}
    if os.path.isfile(yol):
        with open(yol, encoding="utf-8") as f:
            veri = json.load(f) or {}
        shutil.copy2(yol, yol + ".bak-czip")
    hooks = veri.setdefault("hooks", {})
    betik = os.path.join(KOK, "claude_hook.py")
    for olay, (eslesme, sure) in OLAYLAR.items():
        gruplar = [g for g in hooks.get(olay, [])
                   if not any("claude_hook.py" in str(h.get("command", ""))
                              for h in g.get("hooks", []))]
        if not kaldir:
            g = {"hooks": [{"type": "command", "timeout": sure,
                            "command": '"%s" "%s" %s' % (py, betik, olay)}]}
            if eslesme:
                g["matcher"] = eslesme
            gruplar.append(g)
        if gruplar:
            hooks[olay] = gruplar
        else:
            hooks.pop(olay, None)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol + ".tmp", "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
    os.replace(yol + ".tmp", yol)
    print("Claude Code hook'lari %s -> %s" % ("kaldirildi" if kaldir else "kuruldu", yol))


def main(argv):
    py = sys.executable
    if "--python" in argv:
        py = argv[argv.index("--python") + 1]
    yol = os.environ.get("CLAUDE_DESKTOP_CONFIG") or config_yolu()
    veri = {}
    if os.path.isfile(yol):
        with open(yol, encoding="utf-8") as f:
            veri = json.load(f) or {}
        shutil.copy2(yol, yol + ".bak-czip")
    sunucular = veri.setdefault("mcpServers", {})
    if "--kaldir" in argv:
        sunucular.pop("czip", None)
        eylem = "kaldirildi"
    else:
        sunucular["czip"] = {"command": py,
                             "args": [os.path.join(KOK, "mcp_server.py")]}
        eylem = "eklendi"
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
    os.replace(tmp, yol)
    print("Claude Desktop: czip MCP %s -> %s" % (eylem, yol))
    print("Claude Desktop'u yeniden baslat; araclar: yon_karti, hatirla, hafiza_brifing, ...")
    if "--hooks" in argv:
        hooks_kur(py, kaldir="--kaldir" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
