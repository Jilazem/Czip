# -*- coding: utf-8 -*-
"""Otopilot testleri: yon karti, hafiza (gunluk + RAG), baglam koruma hook'lari,
haftalik temizlik, saf-stdlib MCP. Ag / model / gercek HOME gerekmez.
Calistir:  python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import time
import unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

import ayar  # noqa: E402
import claude_hook  # noqa: E402
import depo  # noqa: E402
import hafiza  # noqa: E402
import hkp  # noqa: E402
import temizlik  # noqa: E402
import yon  # noqa: E402

GUN = 86400


def _ortam():
    """Her test icin temiz, izole dizinler (gercek ~/007-HERMES'e asla dokunmaz)."""
    t = tempfile.mkdtemp(prefix="czip-oto-")
    paket = os.path.join(t, "paketler")
    os.makedirs(paket)
    hkp.PAKET_DIZIN = paket
    hkp.KAYIT_YOL = os.path.join(paket, "kayit.json")
    depo.DEPO_YOLU = os.path.join(paket, "czip-index.db")
    ayar.AYAR_YOLU = os.path.join(t, "ayar.json")
    os.environ["CZIP_AYAR"] = ayar.AYAR_YOLU
    os.environ["CZIP_HOOK_DURUM"] = os.path.join(t, "hook")
    os.environ["CZIP_YEDEK_DIZINLERI"] = os.path.join(t, "motor")
    os.makedirs(os.path.join(t, "motor"))
    # arka plan temizligi AYRI surec olarak baslar ve bu yamalari gormez:
    # testte asla kendiliginden baslamasin (gercek HOME'a dokunmasin)
    ayar.yaz(haftalik_temizlik=False)
    return t


def _konusma(n_tur=3, son_yanitsiz=False):
    m = [{"role": "user", "content": "Parsel raporu icin imar modulunu yaz."}]
    for i in range(n_tur):
        m.append({"role": "assistant", "content": "Simdi dosyaya bakiyorum.",
                  "tool_calls": [{"id": "t%d" % i, "function": {
                      "name": "Read", "arguments": json.dumps({"file_path": "/p/imar.py"})}}]})
        m.append({"role": "tool", "content": "def imar(): pass", "tool_call_id": "t%d" % i})
        m.append({"role": "assistant", "content":
                  "SQLite yerine duz JSON kullandim cunku tek kullanici var. "
                  "Kalan: rapor sablonu henuz yazilmadi."})
        m.append({"role": "user", "content": "Tamam, simdi rapor sablonuna gec %d." % i})
    if not son_yanitsiz:
        m.append({"role": "assistant", "content": "Rapor sablonu hazir."})
    return m


def _kayitlar(mesajlar):
    return [hkp.kayit_yap(m, {}) for m in mesajlar]


class TestYonKarti(unittest.TestCase):

    def test_kararlar_tur_sonundan_anlatim_haric(self):
        k = yon.kart(_kayitlar(_konusma()))
        metinler = [s for _, s in k["kararlar"]]
        self.assertTrue(any("cunku tek kullanici" in s for s in metinler))
        self.assertFalse(any("bakiyorum" in s for s in metinler + [s for _, s in k["acik"]]))
        self.assertTrue(any("henuz yazilmadi" in s for _, s in k["acik"]))
        self.assertEqual(k["hedef"][0], 0)
        self.assertIn("/p/imar.py", k["dosyalar"])

    def test_sonraki_adim_yanitsiz_ve_yarim(self):
        k = yon.kart(_kayitlar(_konusma(son_yanitsiz=True)))
        self.assertIn("yanitsiz", k["sonraki"])
        m = _konusma(son_yanitsiz=True) + [
            {"role": "assistant", "content": "Bakiyorum.", "tool_calls": [
                {"id": "z", "function": {"name": "Bash", "arguments": "{}"}}]},
            {"role": "tool", "content": "ok"}]
        k = yon.kart(_kayitlar(m))
        self.assertTrue(k["yarim"])
        self.assertIn("kesildi", k["sonraki"])

    def test_metin_kisa_ve_dogrula(self):
        t = yon.metin(yon.kart(_kayitlar(_konusma())), "abc123")
        self.assertIn("SONRAKI ADIM", t)
        self.assertIn("DOGRULA", t)
        self.assertLess(len(t), 2500)


class TestHafiza(unittest.TestCase):

    def setUp(self):
        self.t = _ortam()

    def _paketle(self, mesajlar, sid, cwd="/p/imar", ad="imar"):
        r = hkp.sikistir(mesajlar, os.path.join(hkp.PAKET_DIZIN, "%s-%s.hkp" % (ad, time.time_ns())),
                         "akilli", baslik=ad, kaynak={"sid": sid, "cwd": cwd})
        kid = hkp.id_ata(r["yol"], ad)
        hafiza.kaydet(r, kid, r["kaynak"])
        return kid, r

    def test_meta_yon_ve_kaynak(self):
        kid, r = self._paketle(_konusma(), "cc:1")
        meta = hkp.meta_oku(r["yol"])
        self.assertEqual(meta["kaynak"]["sid"], "cc:1")
        self.assertEqual(meta["mod"], "akilli")
        self.assertTrue(meta["yon"]["kararlar"])

    def test_gunluk_ve_brifing(self):
        self._paketle(_konusma(), "cc:1")
        kid2, _ = self._paketle(_konusma(4), "cc:1")      # ayni oturum, yeni paket
        self._paketle(_konusma(), "cc:2", cwd="/baska")
        g = hafiza.gunluk(cwd="/p/imar")
        self.assertEqual([o["kid"] for o in g], [kid2])   # oturum basina en yeni
        b = hafiza.brifing(cwd="/p/imar")
        self.assertIn("[CZIP HAFIZA]", b)
        self.assertIn("YON KARTI", b)
        self.assertEqual(hafiza.brifing(cwd="/hic-yok"), "")

    def test_hatirla_alaka_kapisi_ve_turkce(self):
        self._paketle([{"role": "user", "content": "Laya silme eşiği neden 0.15 seçildi?"},
                       {"role": "assistant", "content": "Laya puanı bağlama duyarlı olduğu "
                        "için silme eşiği korumacı tutuldu."}], "cc:9", ad="laya")
        _, isabet = hafiza.hatirlatma("laya silme esigi nasil secildi")
        self.assertTrue(isabet)
        _, isabet = hafiza.hatirlatma("kedi mama fiyatlari nedir acaba")
        self.assertEqual(isabet, [])
        _, isabet = hafiza.hatirlatma("laya silme esigi nasil secildi", haric_sid="cc:9")
        self.assertEqual(isabet, [])


class TestHook(unittest.TestCase):

    def setUp(self):
        self.t = _ortam()
        self.tr = os.path.join(self.t, "oturum.jsonl")
        satirlar = [{"type": "user", "cwd": "/p/imar", "message": {
            "role": "user", "content": "imar modulunu yaz"}}]
        for i in range(3):
            satirlar.append({"type": "assistant", "message": {
                "role": "assistant", "content": [{"type": "text", "text": "tamam %d" % i}],
                "usage": {"input_tokens": 5, "cache_read_input_tokens": 150_000 + i,
                          "cache_creation_input_tokens": 1000, "output_tokens": 10}}})
        with open(self.tr, "w", encoding="utf-8") as f:
            for s in satirlar:
                f.write(json.dumps(s) + "\n")

    def _cagir(self, olay, **kw):
        veri = dict(session_id="s1", transcript_path=self.tr, cwd="/p/imar", **kw)
        c = claude_hook.calistir(olay, veri)
        return json.loads(c)["hookSpecificOutput"]["additionalContext"] if c else ""

    def test_baglam_olc_usage(self):
        self.assertEqual(claude_hook.baglam_olc(self.tr), (151_017, "usage"))

    def test_koruma_bir_kez_paketler(self):
        ayar.yaz(claude_ctx=200_000)                 # 151k/200k = %75
        t = self._cagir("UserPromptSubmit", prompt="devam et lutfen simdi")
        self.assertIn("BAGLAM KORUMA", t)
        self.assertIn("paketlendi", t)
        self.assertEqual(self._cagir("UserPromptSubmit", prompt="devam et lutfen simdi"), "")
        self.assertEqual(len(claude_hook._durum_oku("s1")["paketler"]), 1)

    def test_esik_altinda_sessiz(self):
        ayar.yaz(claude_ctx=200_000, kademeler=[0.9], esik_oran=0.9, kalan_token=0)
        self.assertEqual(self._cagir("UserPromptSubmit", prompt="devam et lutfen"), "")

    def test_precompact_ve_baglanti(self):
        self.assertEqual(claude_hook.calistir("PreCompact", dict(
            session_id="s1", transcript_path=self.tr, cwd="/p/imar", trigger="auto")), "")
        kid = claude_hook._durum_oku("s1")["paketler"][-1]
        t = self._cagir("SessionStart", source="compact")
        self.assertIn("[CZIP BAGLANTISI]", t)
        self.assertIn(kid, t)

    def test_acilis_brifingi(self):
        claude_hook.calistir("PreCompact", dict(session_id="s1", transcript_path=self.tr,
                                                cwd="/p/imar"))
        ayar.yaz(haftalik_temizlik=False)
        self.assertIn("[CZIP HAFIZA]", self._cagir("SessionStart", source="startup"))

    def test_haftalik_temizlik_arka_planda_baslar(self):
        ayar.yaz(haftalik_temizlik=True)
        cagrilar = []
        asil = claude_hook.subprocess.Popen
        claude_hook.subprocess.Popen = lambda argv, **kw: cagrilar.append(argv)
        try:
            t = self._cagir("SessionStart", source="startup")
        finally:
            claude_hook.subprocess.Popen = asil
            ayar.yaz(haftalik_temizlik=False)
        self.assertEqual(cagrilar[0][-3:], ["temizle", "--uygula", "--sessiz"])
        self.assertIn("haftalik temizlik", t)

    def test_bozuk_girdi_asla_patlamaz(self):
        self.assertEqual(claude_hook.calistir("UserPromptSubmit",
                                              {"transcript_path": "/yok/boyle"}), "")
        self.assertEqual(claude_hook.calistir("Bilinmeyen", {}), "")


class TestTemizlik(unittest.TestCase):

    def setUp(self):
        self.t = _ortam()
        self.eski = time.time() - 3 * GUN

    def _paket(self, mesajlar, ad, sid, mod="akilli", yas=3):
        r = hkp.sikistir(mesajlar, os.path.join(hkp.PAKET_DIZIN, ad + ".hkp"), mod,
                         baslik=ad, kaynak={"sid": sid})
        kid = hkp.id_ata(r["yol"], ad)
        zaman = time.time() - yas * GUN
        os.utime(r["yol"], (zaman, zaman))
        return kid, r["yol"]

    def test_yenisi_var_yonlendirir_ve_geri_alir(self):
        kid1, y1 = self._paket(_konusma(2), "a1", "cc:x", yas=3)
        kid2, y2 = self._paket(_konusma(3), "a2", "cc:x", yas=2)
        plan = temizlik.plan()
        self.assertEqual([(x["neden"], x["yol"]) for x in plan], [("yenisi_var", y1)])
        temizlik.uygula(plan)
        self.assertFalse(os.path.exists(y1))
        self.assertEqual(hkp.id_coz(kid1), os.path.abspath(y2))  # eski id calisiyor
        self.assertTrue(hkp.mesaj_araligi(hkp.id_coz(kid1), "0"))
        self.assertEqual(temizlik.geri_al(), 1)
        self.assertTrue(os.path.exists(y1))
        self.assertEqual(hkp.id_coz(kid1), y1)

    def test_eksiksiz_korunur_ve_farkli_is_korunur(self):
        self._paket(_konusma(2), "e1", "cc:y", mod="eksiksiz", yas=3)
        self._paket(_konusma(3), "e2", "cc:y", yas=2)
        self._paket([{"role": "user", "content": "bambaska bir is"}] * 3, "b1", "cc:z", yas=3)
        self._paket(_konusma(3), "b2", "cc:z", yas=2)
        self.assertEqual(temizlik.plan(), [])

    def test_hayalet_yedek_ve_cop_bosaltma(self):
        _, y = self._paket([{"role": "user", "content": "x"}], "h", "cc:h", yas=10)
        motor = os.environ["CZIP_YEDEK_DIZINLERI"]
        for i in range(4):
            p = os.path.join(motor, "hkp.py.bak-%d" % i)
            open(p, "w").write("b")
            os.utime(p, (self.eski - (30 + i) * GUN, self.eski - (30 + i) * GUN))
        yabanci = os.path.join(motor, "benim.txt.bak-1")
        open(yabanci, "w").write("x")
        os.utime(yabanci, (0, 0))
        nedenler = sorted(x["neden"] for x in temizlik.plan())
        self.assertEqual(nedenler, ["eski_yedek", "eski_yedek", "hayalet"])
        r = temizlik.uygula()
        self.assertTrue(os.path.exists(yabanci))
        self.assertEqual(len([f for f in os.listdir(motor) if f.startswith("hkp.py")]), 2)
        self.assertFalse(temizlik.gerekli_mi())
        # cop 31 gun sonra gercekten bosalir
        os.utime(r["cop"], (time.time() - 31 * GUN,) * 2)
        self.assertEqual(temizlik.cop_bosalt(), 1)


class TestMiniMcp(unittest.TestCase):

    def test_jsonrpc(self):
        import mcp_server
        mini = mcp_server._MiniMCP("t")

        @mini.tool()
        def topla(a: int, b: int = 2) -> str:
            """iki sayi"""
            return str(a + b)

        self.assertIsNone(mini.isle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        r = mini.isle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("tools", r["result"]["capabilities"])
        t = mini.isle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"][0]
        self.assertEqual(t["inputSchema"]["required"], ["a"])
        self.assertEqual(t["inputSchema"]["properties"]["a"]["type"], "integer")
        c = mini.isle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "topla", "arguments": {"a": 40}}})
        self.assertEqual(c["result"]["content"][0]["text"], "42")
        self.assertIn("error", mini.isle({"jsonrpc": "2.0", "id": 4, "method": "yok"}))


if __name__ == "__main__":
    unittest.main()
