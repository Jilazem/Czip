# -*- coding: utf-8 -*-
"""Smoke test for the czip context engine. Run with the Hermes interpreter:

    <hermes python> hermes_context_engine/test_czip_engine.py

Uses a temp pack dir and an injected config; never touches HERMES_HOME or
hermes_cli.config (a fresh HERMES_HOME makes newer Hermes run its install
completion, which rebuilds products in the live tree). No LLM or network calls.
"""
import importlib.util
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="czip-engine-test-")
os.environ["CZIP_CTX_DIR"] = os.path.join(TMP, "packs")
HERE = os.path.dirname(os.path.abspath(__file__))

from agent.context_compressor import ContextCompressor  # noqa: E402

TOOL_BODY = "abcdefghij" * 2000


def load(mode):
    name = f"czip_engine_{mode}"
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "czip", "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._config = lambda: {
        "czip": {"mode": mode, "threshold_tokens": 64000},
        "compression": {"protect_first_n": 1, "protect_last_n": 4, "target_ratio": 0.25},
    }

    class Ctx:
        engine = None

        def register_context_engine(self, e):
            self.engine = e

    ctx = Ctx()
    mod.register(ctx)
    e = ctx.engine
    e.update_model(model="GLM-5.3-Flash-EXL3", context_length=262144)
    return e


def conversation(n):
    msgs = [{"role": "system", "content": "You are Hermes. " * 200}]
    for t in range(n):
        msgs += [
            {"role": "user", "content": f"Görev {t}: parsel 1403/{t} emsal analizi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{t}", "type": "function",
                 "function": {"name": "read_file", "arguments": json.dumps({"path": f"/x/{t}.md"})}}]},
            {"role": "tool", "tool_call_id": f"c{t}", "content": f"OUT{t:03d}-" + TOOL_BODY},
            {"role": "assistant", "content": f"Parsel {t} değeri {1000 + t} TL/m2."},
        ]
    return msgs


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        check.failed = True


check.failed = False

for mode, llm in (("map", None), ("hybrid", "LLM SUMMARY TEXT"), ("hybrid", None)):
    ContextCompressor._generate_summary = (
        lambda self, turns, *a, _r=llm, **k: self._with_summary_prefix(_r) if _r else None)
    e = load(mode)
    e.on_session_start(f"sess-{mode}-{llm is not None}")
    msgs = conversation(60)
    out = e.compress(msgs, current_tokens=200000)
    tag = f"[{mode}, llm={'ok' if llm else 'fail'}]"
    print(tag, "mode", e._czip_mode, "threshold", e.threshold_tokens, "tail", e.tail_token_budget)
    check(len(out) < len(msgs), f"{tag} compacted {len(msgs)} -> {len(out)}")
    tools = [m for m in e._czip_archive if m.get("role") == "tool"]
    check(tools and all(len(m["content"]) == len(TOOL_BODY) + 7 for m in tools),
          f"{tag} {len(tools)} archived tool outputs verbatim")
    summ = [m for m in out if "Lossless archive (czip)" in str(m.get("content"))]
    check(len(summ) == 1, f"{tag} handoff carries czip section")
    if llm:
        check("LLM SUMMARY TEXT" in summ[0]["content"], f"{tag} LLM summary kept")
    check("␟CZIP" not in json.dumps(out, ensure_ascii=False), f"{tag} no marker leak")
    hits = json.loads(e.handle_tool_call("czip_search", {"query": "parsel 1403/12"}))["hits"]
    check(any(h["i"] is not None for h in hits), f"{tag} czip_search hits={len(hits)}")
    hint = next((m["content"] for m in out if m["role"] == "tool" and "czip_range '" in m["content"]), "")
    check(bool(hint), f"{tag} pruned tool result in context points at its archive index")
    idx = hint.rsplit("czip_range '", 1)[-1].split("'")[0] if hint else "0"
    rng = json.loads(e.handle_tool_call("czip_range", {"range": idx}))["messages"]
    check(rng and rng[0]["rol"] == "tool" and rng[0]["icerik"].startswith("OUT"), f"{tag} czip_range verbatim")
    check(json.loads(e.handle_tool_call("czip_map", {}))["toplam"] == len(e._czip_archive), f"{tag} czip_map")
    ok, prev = True, None
    for m in out:
        if m["role"] == "tool" and not (prev and prev["role"] in ("assistant", "tool")):
            ok = False
        prev = m
    check(ok, f"{tag} tool pairing")

# second compaction appends, reload from disk restores
e = load("map")
e.on_session_start("sess-two")
out = e.compress(conversation(60), current_tokens=200000)
n1 = len(e._czip_archive)
for t in range(60, 90):
    out += [{"role": "user", "content": f"Görev {t}: devam"},
            {"role": "assistant", "content": f"tamam {t} " + "x" * 4000}]
out2 = e.compress(out, current_tokens=120000)
check(len(e._czip_archive) > n1, f"second compaction appended ({n1} -> {len(e._czip_archive)})")
check(len({json.dumps(m, sort_keys=True) for m in e._czip_archive}) == len(e._czip_archive), "no duplicates")
e2 = load("map")
e2.on_session_start("sess-two")
check(len(e2._czip_archive) == len(e._czip_archive), "archive reloads from disk")

if hasattr(ContextCompressor, "prune_tool_results_only"):
    e3 = load("map")
    e3.on_session_start("sess-prune")
    e3.proactive_prune_tokens = 1000
    res = e3.prune_tool_results_only(conversation(30), current_tokens=500000)
    pruned = res[0] if isinstance(res, tuple) else res
    hinted = [m for m in pruned if "czip_range" in str(m.get("content"))]
    check(bool(e3._czip_archive) and bool(hinted),
          f"proactive prune archived {len(e3._czip_archive)} and hinted {len(hinted)}")
else:
    print("SKIP prune_tool_results_only (not in this Hermes version)")

print("RESULT:", "FAIL" if check.failed else "ALL PASS", "tmp=" + TMP)
sys.exit(1 if check.failed else 0)
