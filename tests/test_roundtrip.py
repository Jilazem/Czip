# -*- coding: utf-8 -*-
"""czip round-trip testi - sentetik veri, dis dosya gerektirmez.
Calistir:  python3 tests/test_roundtrip.py
"""
import json, os, sys, tempfile
BURADA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BURADA))
import hkp

MESJ = [
    {"role": "user", "content": "merhaba", "id": 1, "timestamp": 100.0},
    {"role": "assistant", "content": "selam " + ("tekrar " * 200), "id": 2, "timestamp": 101.0,
     "reasoning": "dusuncen bir sey " + ("tekrar " * 200),
     "reasoning_content": "dusuncen bir sey " + ("tekrar " * 200),
     "tool_calls": [{"id": "c1", "call_id": "c1", "response_item_id": "fc_c1",
                     "type": "function",
                     "function": {"name": "terminal", "arguments": "{\"command\":\"ls\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "tool_name": "terminal",
     "content": "dosya1\ndosya2\ndosya3", "id": 3, "timestamp": 102.0},
    {"role": "assistant", "content": "bitti", "id": 4, "timestamp": 103.0,
     "finish_reason": "stop"},
]

def main():
    gecici = tempfile.mkdtemp(prefix="czip-test-")
    r = hkp.sikistir(MESJ, os.path.join(gecici, "t"), mod="eksiksiz")
    assert r["paket_bayt"] < 6000, r
    ac = hkp.iceri_ac(r["yol"], 0)
    assert ac["ok"] and ac["mesaj"] == 4, ac
    ile = [json.loads(s) for s in ac["satirlar"].split("\n")]
    assert [i["rol"] for i in ile] == ["user", "assistant", "tool", "assistant"]
    assert ile[0]["icerik"] == "merhaba"
    assert ile[2]["icerik"] == "dosya1\ndosya2\ndosya3"
    assert ile[1]["tool_calls"][0]["name"] == "terminal"
    assert "dusuncen bir sey" in ile[1]["reasoning"]
    kil = hkp.kilavuz(r["yol"])
    assert kil["ok"] and kil["toplam"] == 4 and "indeks" in kil
    ar = hkp.mesaj_araligi(r["yol"], "2-3")
    assert len(ar) == 2 and ar[0]["rol"] == "tool" and ar[1]["rol"] == "assistant"
    print("TEST GECTI: round-trip 4/4 + kilavuz + aralik (paket %d B / %d ilet)"
          % (r["paket_bayt"], r["mesaj"]))

if __name__ == "__main__":
    main()
