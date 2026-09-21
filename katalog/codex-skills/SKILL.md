---
name: czip-session-pack
description: "Use when asked to compress, pack or carry an agent session into a new chat, or to read a previously packed session without loading full history (HKP1 packs: czip pack/read/map/search/range)."
---

# czip — pack a session / read a pack without unpacking

When the user wants to carry a session into a new chat (or says "czip"),
run it via the terminal. Never load a whole pack into context.

## Commands (via the terminal tool, verbatim)

1. Pack (active/requested session):
   `czip pack <session_id|last|longest> [--full]`
   - if the user gave no id: find it with something like `hermes sessions list`
     or use `last` (last active session = usually this conversation).
   - the output contains a 6-char SHORT ID (written to kayit.json) — use the
     ID instead of file paths.
2. Read in a new session (WITHOUT unpacking — index + recent messages + status,
   ~40KB):
   `czip read <id|file.hkp|last>`
3. Cheap entry point — RAG work-map (~1.5k tokens, role counts, tool
   histogram, user requests, recent messages):
   `czip map <id|last>`
4. Exact range (full text, tokens resolved):
   `czip range <id|last> <a-b>`
5. Search INSIDE a pack without unpacking (matching message indices + context):
   `czip search <id|last> "query"` → for a hit index i, get full text via
   `czip range <id> i`.
6. Registered packs: `czip list` (id | date | title).

Packs live in: `~/.hermes/session-packs/*.hkp`

## One-click button flow (czip-tasi plugin + context-nobetci cron)

- The `context-nobetci.py` cron (every 15 min, --no-agent) watches each active
  session's `model_config._usage_anchor.prompt_tokens`; threshold =
  max(150k, 30%·context_length) (env: CZIP_ESİK_TOKEN / CZIP_ESİK_ORAN).
- When a session passes the threshold it writes an offer file to
  `~/.hermes/czip-teklifler/<chat>-<thread>.json` (+ a Telegram notice; state:
  ~/.hermes/czip/nobetci.json, 8h sleep).
- When the user sends the NEXT message in that chat, the czip-tasi plugin
  swallows the message and opens a ONE-CLICK picker: 📦 smart / 🧊 full / ❌ cancel.
- On click: pack → `store.reset_session` (the official /new path) → a
  `[CZIP BAGLANTISI]` instruction is injected as the FIRST message of the new
  session (pre_llm_call, one-shot).
- The plugin loads on gateway RESTART (allowlist: plugins.enabled).

## Rules

- From the INDEX in `czip read` / `czip map` output, find the relevant message
  numbers and read only those ranges with `czip range` — never the whole pack.
- "continue where you left off" = the recent-messages section is already given
  in full.
- For lossless carry-over use `--full` (~7x instead of 8.4x).
- The same actions exist as MCP tools: the oturum-sikistirici MCP's
  `oturum_sikistir`, `kilavuz`, `mesajlar` tools (preference order irrelevant).
