#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude_kur.py — czip MCP sunucusunu Claude Desktop'a tanitir.

claude_desktop_config.json'a 'czip' girdisini EKLER (diger sunuculara
dokunmaz), once .bak-czip yedegi alir. Tekrar calistirmak zararsizdir.
Kullanim: python3 scripts/claude_kur.py [--python /yol/python3] [--kaldir]
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
    print("Claude Desktop'u yeniden baslat; araclar: claude_oturum_paketle, hafiza_harita, ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
