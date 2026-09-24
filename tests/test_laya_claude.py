# -*- coding: utf-8 -*-
"""Laya karar kapisi + Claude Code/Desktop koprusu testleri (ag yok, model yok).
Calistir:  python3 -m unittest discover -s tests

Kapsam:
- ccd_dokum: Claude Code transcript.jsonl -> czip iletleri (tool_use/tool_result)
- girdi_ayristir: Claude dokumunu kendiliginden tanir
- karar.sor: sahte Laya isci sureciyle uctan uca; Laya esikleri (sil 0.15)
- bulut_yedegi varsayilan KAPALI: Laya yoksa Jev'e hicbir sey gitmez
- ayar.esik_token, laya_kapi ceviri korumasi, MCP claude araclari
"""
import json
import os
import sys
import tempfile
import types
import unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
TMP = tempfile.mkdtemp(prefix="czip-test-")
os.environ["CZIP_AYAR"] = os.path.join(TMP, "ayar.json")
os.environ.pop("CZIP_KARAR_MOTORU", None)
os.environ.pop("CZIP_BULUT_YEDEGI", None)

import ayar  # noqa: E402
import ccd_dokum  # noqa: E402
import hkp  # noqa: E402
import karar  # noqa: E402
import laya_kapi  # noqa: E402

ayar.AYAR_YOLU = os.environ["CZIP_AYAR"]

UZUN = "satir verisi " * 200  # >1200 B, hata ipucu yok

CC_SATIRLAR = [
    {"type": "custom-title", "customTitle": "Czip Laya gecisi"},
    {"type": "user", "uuid": "u1", "timestamp": "2026-09-24T10:00:00Z",
     "message": {"role": "user", "content": "laya kapisini bagla"}},
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-09-24T10:00:05Z",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "Dosyayi okuyorum."},
         {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x.py"}}]}},
    {"type": "user", "uuid": "u2", "timestamp": "2026-09-24T10:00:06Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "t1", "content": [
             {"type": "text", "text": "print('merhaba')"}]}]}},
    {"type": "attachment", "attachment": {"type": "environment"}},
]

SAHTE_LAYA = r'''
import json, sys
for satir in sys.stdin:
    q = json.loads(satir)["questions"]
    # 'SIL' iceren ornek 0.10 (Laya silme esiginin alti), 'ORTA' 0.20
    # (Jev'de silinirdi, Laya'da kirpilir), digerleri 0.90 (tut)
    cevap = {k: (0.10 if "SIL" in v else 0.20 if "ORTA" in v else 0.90)
             for k, v in q.items()}
    sys.stdout.write(json.dumps({"answers": cevap}) + "\n")
    sys.stdout.flush()
'''


def _sahte_laya():
    yol = os.path.join(TMP, "sahte_laya.py")
    with open(yol, "w", encoding="utf-8") as f:
        f.write(SAHTE_LAYA)
    return yol


class TestClaudeKoprusu(unittest.TestCase):

    def _dokum(self):
        yol = os.path.join(TMP, "proj", "abc123.jsonl")
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        with open(yol, "w", encoding="utf-8") as f:
            for s in CC_SATIRLAR:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
            f.write("{bozuk satir\n")
        return yol

    def test_oku(self):
        mesajlar, baslik = ccd_dokum.oku(self._dokum())
        self.assertEqual(baslik, "Czip Laya gecisi")
        self.assertEqual([m["role"] for m in mesajlar], ["user", "assistant", "tool"])
        self.assertEqual(mesajlar[1]["tool_calls"][0]["function"]["name"], "Read")
        self.assertEqual(mesajlar[2]["tool_call_id"], "t1")
        self.assertIn("merhaba", mesajlar[2]["content"])

    def test_girdi_ayristir_tanir(self):
        mesajlar = hkp.girdi_ayristir(self._dokum())
        self.assertEqual(len(mesajlar), 3)
        self.assertEqual(mesajlar[2]["role"], "tool")

    def test_coz_ve_paketle(self):
        self._dokum()
        ccd_dokum.PROJE_DIZIN = TMP
        self.assertTrue(ccd_dokum.coz("cc:son").endswith(".jsonl"))
        self.assertTrue(ccd_dokum.coz("cc:abc").endswith("abc123.jsonl"))
        self.assertIsNone(ccd_dokum.coz("cc:yok-boyle"))
        mesajlar, baslik = ccd_dokum.oku(ccd_dokum.coz("cc:abc"))
        r = hkp.sikistir(mesajlar, os.path.join(TMP, "cc.hkp"), "eksiksiz", baslik=baslik)
        self.assertTrue(r["ok"])
        self.assertEqual(hkp.mesaj_araligi(r["yol"], "2")[0]["icerik"], "print('merhaba')")


class TestLayaKapisi(unittest.TestCase):

    def setUp(self):
        karar._LAYA.kapat()
        os.environ["CZIP_LAYA_PY"] = sys.executable
        os.environ["CZIP_LAYA_KAPI"] = _sahte_laya()

    def tearDown(self):
        karar._LAYA.kapat()
        for k in ("CZIP_LAYA_PY", "CZIP_LAYA_KAPI", "TYPESAFE_API_KEY"):
            os.environ.pop(k, None)

    def test_varsayilan_motor_laya(self):
        self.assertEqual(karar.motor_adi(), "laya")
        self.assertEqual(karar.esikler()["sil"], 0.15)
        self.assertFalse(ayar.oku()["bulut_yedegi"])

    def test_sor(self):
        cevap, motor = karar.sor("durum", {"a": "SIL bunu", "b": "kalsin"})
        self.assertEqual(motor, "laya")
        self.assertEqual(cevap, {"a": 0.10, "b": 0.90})

    def test_sikistir_laya_ile(self):
        mesajlar = [{"role": "user", "content": "is"},
                    {"role": "tool", "content": "SIL " + UZUN},
                    {"role": "tool", "content": "ORTA " + UZUN},
                    {"role": "tool", "content": "TUT " + UZUN}]
        r = hkp.sikistir(mesajlar, os.path.join(TMP, "laya.hkp"), "akilli",
                         arac_bas=100, arac_son=100, jev=True)
        self.assertEqual(r["jev"]["motor"], "laya")
        self.assertEqual((r["jev"]["sil"], r["jev"]["kirp"], r["jev"]["tut"]), (1, 1, 1))
        it = hkp.mesaj_araligi(r["yol"], "1-3")
        self.assertTrue(it[0]["icerik"].startswith("[LAYA-SILINDI noul=0.1"))
        self.assertLess(len(it[1]["icerik"]), len("ORTA " + UZUN))   # kirpildi
        self.assertEqual(it[2]["icerik"], ("TUT " + UZUN).strip())   # aynen (bosluk temizligi haric)

    def test_laya_yoksa_buluta_gitmez(self):
        os.environ["CZIP_LAYA_PY"] = os.path.join(TMP, "yok", "python")
        os.environ["TYPESAFE_API_KEY"] = "sahte"
        asil = hkp._jev_batch
        hkp._jev_batch = lambda *a, **k: self.fail("bulut yedegi kapaliyken Jev cagrildi")
        try:
            with self.assertRaises(ValueError):
                karar.sor("durum", {"a": "x"})
            r = hkp.sikistir([{"role": "tool", "content": UZUN}],
                             os.path.join(TMP, "statik.hkp"), "akilli",
                             arac_bas=100, arac_son=100, jev=True)
            self.assertIn("laya_yok", r["jev"]["hata"])
            self.assertEqual(len(r["eksik_bildirim"]), 1)   # statik dilime dustu
        finally:
            hkp._jev_batch = asil

    def test_bulut_yedegi_acikken_jeve_duser(self):
        os.environ["CZIP_LAYA_PY"] = os.path.join(TMP, "yok", "python")
        os.environ["TYPESAFE_API_KEY"] = "sahte"
        os.environ["CZIP_BULUT_YEDEGI"] = "1"
        asil = hkp._jev_batch
        hkp._jev_batch = lambda key, d, s, t: {q: 0.5 for q in s}
        try:
            cevap, motor = karar.sor("durum", {"a": "x"})
            self.assertEqual((cevap, motor), ({"a": 0.5}, "jev"))
        finally:
            hkp._jev_batch = asil
            os.environ.pop("CZIP_BULUT_YEDEGI", None)


class TestCli(unittest.TestCase):

    def test_harita_cevre_ve_hata(self):
        import contextlib
        import io
        hkp.KAYIT_YOL = os.path.join(TMP, "kayit.json")
        mesajlar = [{"role": "user" if i % 2 == 0 else "assistant", "content": "ileti %d" % i}
                    for i in range(10)]
        r = hkp.sikistir(mesajlar, os.path.join(TMP, "cli.hkp"), "akilli")
        kid = hkp.id_ata(r["yol"], "cli")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(hkp._main(["map", kid]), 0)
            self.assertEqual(hkp._main(["around", kid, "5", "--n=1"]), 0)
            self.assertEqual(hkp._main(["map", "yok-boyle"]), 2)
        cikti = buf.getvalue()
        self.assertIn("COST map~", cikti)
        self.assertIn('=> {"i": 5', cikti)
        self.assertIn('{"i": 4', cikti)
        self.assertIn("HATA: paket bulunamadi", cikti)


class TestAyarVeCeviri(unittest.TestCase):

    def test_esik_token(self):
        a = dict(ayar.VARSAYILAN, esik_oran=0.90)
        self.assertEqual(ayar.esik_token(262_000, a), 192_000)     # kalan_token once gelir
        self.assertEqual(ayar.esik_token(1_050_000, a), 945_000)   # oran once gelir
        a["mutlak_token"] = 64_000
        self.assertEqual(ayar.esik_token(1_050_000, a), 64_000)

    def test_bozuk_ceviri(self):
        kaynak = "Bu dosya parsel kaydını içeriyor " * 10
        self.assertTrue(laya_kapi._bozuk_ceviri(kaynak, "..."))
        self.assertTrue(laya_kapi._bozuk_ceviri(kaynak, "kisa"))
        self.assertFalse(laya_kapi._bozuk_ceviri(kaynak, "This file contains the parcel record " * 9))

    def test_isle_sahte_ajan(self):
        class Ajan:
            def predict(self, state, sorular):
                return {"answers": {q: {"noul": 0.7, "confidence": 0.7} for q in sorular}}
        laya_kapi._agent, laya_kapi.CEVIRI_ACIK = Ajan(), False
        r = laya_kapi.isle({"state": "s", "questions": {"q1": "bu gerekli mi?"}})
        self.assertEqual(r["answers"], {"q1": 0.7})


class TestMcpClaude(unittest.TestCase):

    def test_claude_araclari(self):
        # mcp paketi olmadan: FastMCP'yi dekoratoru aynen donduren sahte ile degistir
        fm = types.ModuleType("mcp.server.fastmcp")
        fm.FastMCP = lambda ad: types.SimpleNamespace(tool=lambda: (lambda f: f),
                                                      run=lambda: None)
        for ad in ("mcp", "mcp.server"):
            sys.modules.setdefault(ad, types.ModuleType(ad))
        sys.modules["mcp.server.fastmcp"] = fm
        import mcp_server
        TestClaudeKoprusu()._dokum()
        ccd_dokum.PROJE_DIZIN = TMP
        hkp.PAKET_DIZIN = os.path.join(TMP, "paketler")
        hkp.KAYIT_YOL = os.path.join(TMP, "kayit.json")
        liste = json.loads(mcp_server.claude_oturumlari())
        self.assertEqual(liste[0]["id"], "abc123")
        r = json.loads(mcp_server.claude_oturum_paketle("abc"))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(len(r["id"]), 6)


if __name__ == "__main__":
    unittest.main()
