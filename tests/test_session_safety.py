"""Cross-session, concurrent storage and bilingual command regression tests."""
import concurrent.futures
import contextvars
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hkp
import czip_runtime as runtime
import czip_commands

spec = importlib.util.spec_from_file_location("czip_command_test", ROOT / "plugin/__init__.py")
plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
plugin._MOTOR = str(ROOT / "hkp.py")
CALLER = contextvars.ContextVar("test_czip_caller", default={})


def session_env(key, default=""):
    return CALLER.get().get(key, default)


class SessionSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = self.root / "state.db"
        self.packages = self.root / "packs"; self.packages.mkdir()
        language_patch = patch.dict(os.environ, {"CZIP_LANG":"en"})
        language_patch.start(); self.addCleanup(language_patch.stop)
        self.stack = []
        for replacement in [patch.object(hkp, "PAKET_DIZIN", str(self.packages)),
                            patch.object(hkp, "KAYIT_YOL", str(self.packages / "kayit.json")),
                            patch.object(hkp, "session_env", session_env),
                            patch.object(runtime, "session_env", session_env)]:
            replacement.start(); self.addCleanup(replacement.stop)
        self.set_caller("a")
        with sqlite3.connect(self.db) as db:
            db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,session_key TEXT,ended_at REAL,end_reason TEXT,archived INTEGER);"
                             "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
            for i, sid in enumerate(["a", "a-newer", "b", "with_under", "withXunder"]):
                db.execute("INSERT INTO sessions VALUES(?,?,?,NULL,NULL,0)", (sid, "Same title", "key-" + sid))
                db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES(?,?,?,?)",
                           (sid, "user", "UNIQUE_SOURCE_" + sid, i + 1))

    def set_caller(self, sid, home=None):
        CALLER.set({"HERMES_SESSION_ID": sid, "HERMES_HOME": home or str(self.root),
                    "HERMES_STATE_DB": str(self.db)})

    def test_active_is_caller_not_latest_database_message(self):
        self.assertEqual(hkp.oturum_oku("active")[0], "a")
        self.assertEqual(hkp.oturum_oku("last")[0], "a")
        self.assertEqual(hkp.oturum_oku("a")[0], "a")  # exact ID wins over a-newer

    def test_sql_wildcards_do_not_select_another_session(self):
        self.assertEqual(hkp.oturum_oku("with_under")[0], "with_under")
        with self.assertRaises(ValueError): hkp.oturum_oku("with%")

    def test_missing_context_never_guesses(self):
        CALLER.set({})
        self.assertIn("context", plugin._invoke("pack", "").lower())
        self.assertIn("ID", plugin._invoke("read", ""))

    def test_codex_selector_cannot_open_hermes_last_session(self):
        CALLER.set({"CODEX_THREAD_ID": "codex-own", "HERMES_STATE_DB": str(self.db)})
        with self.assertRaisesRegex(ValueError, "Codex"):
            hkp.oturum_oku("son")

    def test_two_callers_keep_their_own_last_package(self):
        a, _, _ = plugin._packed("")
        self.set_caller("b"); b, _, _ = plugin._packed("")
        self.assertNotEqual(a["yol"], b["yol"])
        self.assertIn("UNIQUE_SOURCE_b", plugin._okur(""))
        self.set_caller("a")
        self.assertIn("UNIQUE_SOURCE_a", plugin._okur(""))
        self.assertNotIn("UNIQUE_SOURCE_b", plugin._okur(""))

    def test_same_session_id_in_another_profile_cannot_read_first_profile_pack(self):
        plugin._packed("")
        self.set_caller("a", str(self.root / "other-profile"))
        self.assertIsNone(hkp.son_paket())
        self.assertIn("explicit ID", plugin._invoke("read", ""))

    def test_parallel_callers_have_distinct_packages_and_bindings(self):
        def run(sid):
            self.set_caller(sid)
            result, _, _ = plugin._packed("")
            return sid, result["yol"], hkp.son_paket()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(run, ["a", "b"]))
        self.assertEqual(len({r[1] for r in rows}), 2)
        self.assertTrue(all(path == last for _, path, last in rows))

    def test_no_automatic_merge_from_pack_command(self):
        with self.assertRaisesRegex(ValueError, "merge"):
            plugin._packed("--auto")
        self.assertEqual(hkp._kayit_oku(), {})

    def test_menu_has_one_entry_per_operation(self):
        class Context:
            def __init__(self): self.items = []
            def register_command(self, name, handler, **metadata): self.items.append((name, handler, metadata))
        context = Context(); plugin.register(context)
        normalized = [name.replace("_", "-") for name, _, _ in context.items]
        self.assertEqual(len(normalized), len(set(normalized)))
        self.assertEqual(len(normalized), len(czip_commands.COMMANDS))
        with runtime.output_language("tr"):
            self.assertIn("Türkçe", plugin._yardim_metni())
        with runtime.output_language("en"):
            self.assertIn("English", plugin._yardim_metni())

    def test_both_languages_resolve_to_same_operation(self):
        for name, aliases, *_ in czip_commands.COMMANDS:
            for alias in aliases:
                self.assertEqual(czip_commands.resolve(alias), name)

    def test_locale_is_dynamic_and_can_be_overridden(self):
        with patch.dict(os.environ, {"CZIP_LANG":"", "LC_ALL":"tr_TR.UTF-8", "LC_MESSAGES":"", "LANG":"en_US.UTF-8"}):
            self.assertEqual(runtime.T("English", "Türkçe"), "Türkçe")
            with runtime.output_language("en"):
                self.assertEqual(runtime.T("English", "Türkçe"), "English")

    def test_tr_and_en_requests_do_not_change_each_other(self):
        import threading
        barrier = threading.Barrier(2)
        def run(language):
            with runtime.output_language(language):
                barrier.wait(timeout=5)
                return runtime.T("English", "Türkçe")
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(run, ["tr", "en"])), ["Türkçe", "English"])

    def test_damaged_registry_is_not_silently_replaced(self):
        path = Path(hkp.KAYIT_YOL); path.write_text("broken")
        with self.assertRaises(ValueError): hkp.id_ata(str(self.packages / "x.hkp"))
        self.assertEqual(path.read_text(), "broken")

    def test_concurrent_processes_do_not_lose_registry_entries(self):
        code = "import hkp,sys; hkp.id_ata(sys.argv[1])"
        env = {**os.environ, "CZIP_KAYIT_YOL":hkp.KAYIT_YOL, "CZIP_PAKET_DIZIN":str(self.packages)}
        children = [subprocess.Popen([sys.executable,"-c",code,str(self.packages / (str(i)+".hkp"))],
                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(8)]
        for child in children:
            _, error = child.communicate(timeout=20); self.assertEqual(child.returncode, 0, error)
        self.assertEqual(len(hkp._kayit_oku()), 8)

    def test_clef_timeout_does_not_hang_or_send_to_another_provider(self):
        command = json.dumps([sys.executable, "-c", "import time; time.sleep(10)"])
        with patch.dict(os.environ, {"CZIP_CLEF_COMMAND":command}):
            with self.assertRaisesRegex(ValueError, "clef_unavailable"):
                hkp.karar_batch("synthetic", {"one":"question"}, timeout=.1, motor="clef")

    def test_clef_rejects_responses_for_another_request(self):
        command = json.dumps([sys.executable,"-c",'print(\'{"answers":{"other":0.9}}\')'])
        with patch.dict(os.environ, {"CZIP_CLEF_COMMAND":command}):
            with self.assertRaisesRegex(ValueError, "unknown_question"):
                hkp.karar_batch("synthetic", {"one":"question"}, motor="clef")

    def test_end_archives_only_its_captured_source_and_undo_restores_it(self):
        import birlestir
        result = plugin._son("")
        self.assertIn("archived: a", result)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT id FROM sessions WHERE archived=1").fetchall(), [("a",)])
        undo = next((self.root / "czip" / "undo").glob("*.json"))
        birlestir.geri_al(str(undo))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NOT NULL OR archived=1").fetchone()[0], 0)

    def test_undo_cannot_cross_profiles_or_overwrite_later_changes(self):
        import birlestir
        package, _, _ = plugin._packed("")
        result = birlestir.pasife_al(["a"], package["yol"])
        self.set_caller("a", str(self.root / "other"))
        with self.assertRaisesRegex(ValueError, "another profile"):
            birlestir.geri_al(result["gerial"])
        self.set_caller("a")
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE sessions SET end_reason='later_change' WHERE id='a'")
        with self.assertRaisesRegex(ValueError, "changed"):
            birlestir.geri_al(result["gerial"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT end_reason FROM sessions WHERE id='a'").fetchone()[0], "later_change")

    def test_failed_undo_write_rolls_back_session_closure(self):
        import birlestir
        package, _, _ = plugin._packed("")
        with patch.object(runtime, "atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): birlestir.pasife_al(["a"], package["yol"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT ended_at,archived FROM sessions WHERE id='a'").fetchone(), (None,0))

    def test_task_last_is_scoped_and_same_titles_never_overwrite(self):
        import gorev
        with patch.object(gorev, "KAYIT_YOL", str(self.packages / "gorev.json")):
            a = gorev.kaydet("promt", "Same title", "source-a")
            self.set_caller("b")
            with self.assertRaises(ValueError): gorev.oku("last")
            b = gorev.kaydet("prompt", "Same title", "source-b")
            self.assertNotEqual(a["yol"], b["yol"])
            self.assertEqual(gorev.oku("last")["metin"], "source-b")
            self.set_caller("a")
            self.assertEqual(gorev.oku("son")["metin"], "source-a")

    def cli(self, *args, **extra):
        env = {**os.environ, **CALLER.get(), "CZIP_PAKET_DIZIN":str(self.packages),
               "CZIP_KAYIT_YOL":hkp.KAYIT_YOL, "CZIP_LANG":"en", **extra}
        return subprocess.run([sys.executable, str(ROOT / "czip_cli.py"), *args], capture_output=True, text=True, env=env, timeout=15)

    def test_cli_full_json_is_strict_and_languages_select_same_source(self):
        for verb, flag, lang in [("pack","--full","en"),("paketle","--eksiksiz","tr")]:
            result = self.cli(verb, "a", flag, "--json", "--lang", lang)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(data["source_id"], "a")
            self.assertEqual(hkp.mesaj_araligi(data["yol"], "0")[0]["icerik"], "UNIQUE_SOURCE_a")
        result = self.cli("help", "--dil", "tr")
        self.assertIn("Türkçe", result.stdout)
        self.assertNotIn("FILE", result.stdout)

    def test_background_packer_only_packs_closed_profile_sessions_once(self):
        with sqlite3.connect(self.db) as db:
            db.executescript("ALTER TABLE sessions ADD COLUMN hidden INTEGER DEFAULT 0; ALTER TABLE sessions ADD COLUMN message_count INTEGER DEFAULT 1; ALTER TABLE sessions ADD COLUMN last_activity_at REAL DEFAULT 0;")
            db.execute("UPDATE sessions SET ended_at=1 WHERE id='b'")
        env = {**os.environ, **CALLER.get(), "CZIP_PAKET_DIZIN":str(self.packages), "CZIP_KAYIT_YOL":hkp.KAYIT_YOL,
               "CZIP_AUTO_MIN_MESSAGES":"1", "CZIP_AUTO_IDLE_HOURS":"0", "CZIP_OTO":"1"}
        for _ in range(2):
            result = subprocess.run([sys.executable,str(ROOT / "czip_auto.py")],capture_output=True,text=True,env=env,timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.packages.glob("*.hkp"))), 1)
        self.assertIsNone(hkp.son_paket())  # background result never binds to inherited caller a
        self.set_caller("b"); self.assertIn("UNIQUE_SOURCE_b", plugin._okur(""))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT ended_at,archived FROM sessions WHERE id='a'").fetchone(), (None,0))

    def test_unavailable_gate_cannot_approve_a_merge(self):
        import birlestir
        pair = (.95, {"sid":"a","ozet":"similar"}, {"sid":"b","ozet":"similar"})
        with patch.object(birlestir, "_jev_ayni_is", return_value=({}, {"hata":"unavailable"})):
            groups, trace = birlestir.gruplari_kur([pair], jev=True)
        self.assertEqual(groups, [])
        self.assertFalse(trace["kararlar"][0]["kabul"])

    def test_lossless_preserves_whitespace_json_text_and_reasoning(self):
        content = '  { "spacing":  "exact" }\r\n\r\n\r\n  end\t  '
        reasoning = '  think\r\n\r\n\r\n   twice  '
        argument = '{  "path": "a  b", "note": " spaced "  }'
        message = {"role":"assistant", "content":content, "reasoning":reasoning,
                   "reasoning_content":reasoning, "finish_reason":"stop", "effect_disposition":"held",
                   "tool_calls":[{"id":"t", "function":{"name":"f","arguments":argument}}]}
        pack = hkp.sikistir([message], str(self.packages / "full"), mod="full")
        restored = hkp.mesaj_araligi(pack["yol"], "0")[0]
        self.assertEqual(restored["icerik"], content)
        self.assertEqual(restored["reasoning"], reasoning)
        self.assertEqual(restored["reasoning2"], reasoning)
        self.assertEqual(restored["tool_calls"][0]["arguments"], argument)
        self.assertEqual(restored["finish_reason"], "stop")
        self.assertEqual(restored["meta"]["effect_disposition"], "held")

    def test_contextless_last_never_opens_a_random_source_or_pack(self):
        plugin._packed("")
        CALLER.set({"HERMES_STATE_DB":str(self.db)})
        self.assertIsNone(hkp.son_paket())
        with self.assertRaises(ValueError): hkp.oturum_oku("last")

    def test_raw_codex_file_is_parsed_without_selecting_a_hermes_session(self):
        source = self.root / "rollout-codex.jsonl"
        events = [{"type":"session_meta", "payload":{"id":"specific-codex"}},
                  {"type":"response_item", "payload":{"type":"message", "role":"user", "content":[{"type":"input_text","text":"ONLY_THIS_CODEX"}]}},
                  {"type":"response_item", "payload":{"type":"function_call_output", "call_id":"tool-one", "output":"ONLY_THIS_TOOL"}}]
        source.write_text("\n".join(json.dumps(e) for e in events)+"\n")
        before = source.read_bytes()
        result = self.cli("pack",str(source),"--json")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        packed = json.loads(result.stdout)
        self.assertEqual(packed["source_id"], "file:" + str(source))
        self.assertIn("ONLY_THIS_CODEX", json.dumps(hkp.mesaj_araligi(packed["yol"],"0-1")))
        self.assertIn("ONLY_THIS_TOOL", json.dumps(hkp.mesaj_araligi(packed["yol"],"0-1")))
        self.assertEqual(source.read_bytes(), before)

    def test_settings_are_profile_scoped_and_prompt_hook_does_not_intercept_chat(self):
        import ayar
        ayar.yaz(esik_oran=.65)
        self.set_caller("b", str(self.root / "other-profile"))
        self.assertEqual(ayar.oku()["esik_oran"], .5)
        self.set_caller("a"); self.assertEqual(ayar.oku()["esik_oran"], .65)
        spec = importlib.util.spec_from_file_location("prompt_hook", ROOT / "plugins-extra/czip-promt/__init__.py")
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from types import SimpleNamespace
        self.assertIsNone(module.rewrite_gateway_message(SimpleNamespace(text="Please fix my project")))
        self.assertIsNone(module.rewrite_gateway_message(SimpleNamespace(text="czip prompt read abc123")))
        self.assertIn("usable prompt", module.rewrite_gateway_message(SimpleNamespace(text="czip prompt design a menu"))["text"])

    def test_malformed_gate_outputs_are_rejected(self):
        for response in ([], {"answers":{}}, {"answers":{"one":True}}, {"answers":{"one":.9},"confidence":[]}):
            command = json.dumps([sys.executable,"-c","print("+repr(json.dumps(response))+")"])
            with patch.dict(os.environ,{"CZIP_CLEF_COMMAND":command}):
                with self.assertRaises(ValueError): hkp.karar_batch("synthetic",{"one":"question"},motor="clef")

    def test_handover_proof_binds_exact_source_path_and_database(self):
        pack, _, _ = plugin._packed("")
        self.assertTrue(hkp.verify_package_source(pack["kid"], pack["yol"], "a")["ok"])
        with self.assertRaises(ValueError): hkp.verify_package_source(pack["kid"],pack["yol"],"b")
        with self.assertRaises(ValueError): hkp.verify_package_source(pack["kid"],str(self.root / "wrong.hkp"),"a")
        values = dict(CALLER.get()); values["HERMES_STATE_DB"] = str(self.root / "another.db"); CALLER.set(values)
        with self.assertRaises(ValueError): hkp.verify_package_source(pack["kid"],pack["yol"],"a")

    def test_sentinel_never_overwrites_new_route_offer_with_an_older_session(self):
        with patch.dict(os.environ,{"HERMES_HOME":str(self.root),"HERMES_STATE_DB":str(self.db),"TELEGRAM_BOT_TOKEN":"synthetic"}):
            spec = importlib.util.spec_from_file_location("sentinel",ROOT / "scripts/context-nobetci.py")
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            rows = [{"id":sid, "chat_id":"synthetic-chat","thread_id":None,"session_key":"route", "title":"Example", "display_name":None} for sid in ("new", "old")]
            with patch.object(module,"_aktifler",return_value=rows), patch.object(module,"_kullanim",return_value=(950000,1000000)), patch.object(module,"_telegram_gonder") as send:
                self.assertEqual(module.main(["--dry"]),0)
                self.assertFalse((self.root / "czip/nobetci.json").exists())
                self.assertFalse(send.called)
                self.assertEqual(module.main([]),0)
                self.assertEqual(send.call_count,1)
            offer = json.loads((self.root / "czip-teklifler/synthetic-chat-x.json").read_text())
            self.assertEqual(offer["sid"],"new")
            self.assertFalse(offer["oto"])
            self.assertEqual(offer["database"],str(self.db.resolve()))

    def test_map_is_bounded_but_range_preserves_large_content(self):
        text = " start  " + "x" * 60000 + "  end\r\n"
        source = [{"role":"user","content":text,"api_content":"private-context"*50000} for _ in range(8)]
        pack = hkp.sikistir(source, str(self.packages / "large"), mod="full")
        overview = hkp.harita(pack["yol"])
        self.assertLess(len(json.dumps(overview)), 14000)
        self.assertTrue(all(row["kirpildi"] for row in overview["son"]))
        self.assertNotIn("private-context",json.dumps(overview))
        empty = hkp.harita(pack["yol"],son_n=0,istek_max=0)
        self.assertEqual(empty["son"],[]); self.assertEqual(empty["istekler"],[])
        self.assertEqual(hkp.mesaj_araligi(pack["yol"],"7")[0]["icerik"],text)

    def test_literal_codec_markers_cannot_change_source_text(self):
        repeated = "ordinary repeated text " * 40
        literal = "literal codec example: " + hkp._jeton(0)
        source = [{"role":"user","content":repeated},{"role":"user","content":repeated},
                  {"role":"assistant","content":literal}]
        packed = hkp.sikistir(source,str(self.packages / "literal"),baslik="Verified title")
        self.assertEqual(hkp.mesaj_araligi(packed["yol"],"2")[0]["icerik"],literal)
        self.assertEqual(hkp.harita(packed["yol"])["baslik"],"Verified title")

    def test_cli_end_uses_exact_id_and_preserves_other_sources(self):
        result = self.cli("end","a")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT id FROM sessions WHERE archived=1").fetchall(),[("a",)])
