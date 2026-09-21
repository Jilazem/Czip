# -*- coding: utf-8 -*-
"""Manifest testleri — hedef katalog basvuru dosyalarinin gecerliligi.
Calistir:  python3 -m unittest discover -s tests

Kapsam:
- .claude-plugin/plugin.json : Claude Code manifesti (zorunlu alanlar + license beyani)
- .grok-plugin/marketplace.json : Grok Build girdi taslagi (zorunlu alanlar + sha bicimi)
- EN beceri + codex kopyasi : frontmatter tutarliligi
- Lisans dosyasi icerikte 'license' beyani tasir
"""
import json
import os
import re
import unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _yol(*p):
    return os.path.join(KOK, *p)


def _oku_json(*p):
    with open(_yol(*p), encoding="utf-8") as f:
        return json.load(f)


class TestManifestlar(unittest.TestCase):

    def test_claude_plugin_json(self):
        d = _oku_json(".claude-plugin", "plugin.json")
        for alan in ("name", "description", "version", "author"):
            self.assertIn(alan, d, ".claude-plugin/plugin.json: %s yok" % alan)
        self.assertEqual(d["name"], "czip")
        self.assertEqual(d["version"], "1.0.0")
        # 4 hedefin tamaminda lisans tanimi icerik + beyan isteniyor
        self.assertTrue(d.get("license"), "license beyani yok")

    def test_grok_marketplace_girdisi(self):
        d = _oku_json(".grok-plugin", "marketplace.json")
        for alan in ("name", "description", "category", "source", "homepage",
                     "keywords", "domains"):
            self.assertIn(alan, d, "marketplace.json: %s yok" % alan)
        self.assertEqual(d["source"]["url"], "https://github.com/Jilazem/Czip.git")
        sha = d["source"]["sha"]
        # CONTRIBUTING.md: tam 40-haneli sha zorunlu, kisa sha RED
        self.assertRegex(sha, r"^[0-9a-f]{40}$", "sha tam 40 haneli olmali")

    def test_en_beceri_ve_codex_kopyasi(self):
        yollar = [_yol("skills", "czip-session-pack", "SKILL.md"),
                  _yol("katalog", "codex-skills", "SKILL.md")]
        for yol in yollar:
            with open(yol, encoding="utf-8") as f:
                metin = f.read()
            m = re.match(r"^---\n(.*?)\n---\n", metin, re.S)
            if not m:
                self.fail("%s: frontmatter yok" % yol)
            on = m.group(1)
            self.assertIn("name: czip-session-pack", on)
            self.assertIn("description:", on)

    def test_lisans_beyani(self):
        with open(_yol("LICENSE"), encoding="utf-8") as f:
            icerik = f.read().lower()
        self.assertIn("license", icerik)


if __name__ == "__main__":
    unittest.main()
