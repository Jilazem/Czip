# -*- coding: utf-8 -*-
"""czip gizli bilgi maskesi testi - sentetik veri.
Calistir:  python3 tests/test_gizli.py
"""
import json, os, sys, tempfile
BURADA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BURADA))
import hkp

ANAHTARLAR = [
    "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2",
    "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "servis_llm_" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1",
]


def main():
    for a in ANAHTARLAR:
        assert "[GIZLI]" in hkp.gizli_maskele("anahtar: " + a) and a not in hkp.gizli_maskele(a), a
    assert hkp.gizli_maskele('"api_key": "0123456789abcdefXYZ"') == '"api_key": "[GIZLI]"'
    for temiz in ("token sayisi 1500", "sk-ruh", "normal metin", "sha256 a8aa68f7abf126d52e80a2b31a9742a551ab136a"):
        assert hkp.gizli_maskele(temiz) == temiz, temiz
    mesaj = [
        {"role": "user", "content": "anahtarim " + ANAHTARLAR[0], "id": 1, "timestamp": 1.0},
        {"role": "assistant", "content": "tamam", "id": 2, "timestamp": 2.0,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "terminal", "arguments": json.dumps({"command": "export T=" + ANAHTARLAR[1]})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "AWS " + ANAHTARLAR[2], "id": 3, "timestamp": 3.0},
    ]
    g = tempfile.mkdtemp(prefix="czip-gizli-")
    r = hkp.sikistir(mesaj, os.path.join(g, "t"), mod="eksiksiz")
    ac = hkp.iceri_ac(r["yol"], 0)
    assert ac["ok"], ac
    for a in ANAHTARLAR[:3]:
        assert a not in ac["satirlar"], a
    assert "[GIZLI]" in ac["satirlar"]
    r2 = hkp.sikistir(mesaj, os.path.join(g, "t2"), mod="eksiksiz", maskele=False)
    assert ANAHTARLAR[0] in hkp.iceri_ac(r2["yol"], 0)["satirlar"]
    print("TEST GECTI: gizli maske (kalip + atama + paket ici + maskele=False)")


if __name__ == "__main__":
    main()
