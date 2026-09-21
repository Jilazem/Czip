# czip — infinite context, without an infinite context window

> 🌍 English (primary) · [Türkçe](README_tr.md)

When you carry a long agent session into a new chat, the entire history
reloads into context. A 4 MB session = a 4 MB token bill.

**czip compresses an agent's full history into a persistent, searchable
pack (HKP1) — and the new session never unpacks it.** It loads a
~1,500-token map, then retrieves only the message ranges it actually
needs.

![czip hero](assets/kapak-hero.png)

## Headline

> **93,417 → 1,561 tokens. ~60× lower context-loading cost.**
> Measured on a real 3,421-message working session (tiktoken `o200k_base`).

czip does **not** make the model's native context window infinite.
It keeps long history persistent and searchable, and retrieves only the
relevant parts when needed — *practically unbounded retrievable
history.*

**Finite context. Persistent memory.**

## v3 — the read cost was the real bottleneck

Compression ratio turned out to be the wrong metric: the pack file never
enters context at all. The cost is what the reading agent *pulls out*.
Measured on a real 3,421-message session:

| Read path | Tokens |
|---|---|
| `czip read` (full index) | 93,417 |
| `czip map` (RAG map) | **1,561** |
| + targeted `czip search "<query>"` | +634 |

**60× cheaper.** `map` does not dump the transcript — it returns a work
map: role counts, tool histogram, user requests, recent messages, and a
search instruction. The reading agent then pulls exact ranges on demand:
`search` → `range`.

Two ideas were measured and **rejected with data**:

- **Codec tuning.** LZMA2 `pb=0 lc=4 dict=256MB` gained only **1.2%**.
  bz2 was 27% worse. The current `preset=9|EXTREME` is already at the
  limit.
- **Instruction language.** Same instruction: Turkish 39 tokens, English
  34, Chinese 35 — and the instruction text is **0.1%** of total cost.
  The win is not in language, it is in **JSON ceremony**: rendering
  recent messages as `A> text` instead of JSON objects gained 47%.

## v3 — duplicate elimination

Tool outputs are **71%** of a typical pack, and **78% of them are exact
duplicates** (8,571 outputs → 1,854 unique). Second copies are replaced
with `[SAME-#N]` markers.

```
12,217,023 → 479,724 B   (previously 562,416)   ratio 21.7x → 25.5x
```

Across five real sessions: **39.8 MB → 1.44 MB** (15–35×), 2,590
duplicates eliminated.

## v3 — merging (`czip merge`)

Detects sessions doing the same job and consolidates them into **one
pack**; optionally archives the sources (close + archive; messages are
never deleted, `czip undo` restores them).

The decision is made by **Jev** — and it actually matters: local
similarity wanted to merge two unrelated case files at 0.75, **Jev
scored it 0.13 and rejected it**; a true copy was approved at 0.97.
Scheduled-task boilerplate is filtered out (41 false matches → 20).

```
czip merge               # read-only candidate scan
czip merge auto --jev    # merge + archive sources
czip undo <file>         # restore
```

`czip pack` also warns after packing when "another session did this job
too" (the decision again belongs to Jev).

## Numbers (from real sessions, evidence-backed)

| Session | Raw | Pack | Ratio |
|---|---|---|---|
| Game dev (1,117 msgs, 31 long tool outputs) | 4.27 MB | 237 KB | **18.0×** |
| Code / research (210 msgs) | 997 KB | 119 KB | **8.4×** |
| New session's context entry | instead of 4.27 MB | **~40 KB** index + recent msgs | |

## Why never unpack?

Traditional compaction either rewrites the whole history into the new
session (expensive) or summarizes it (lossy). czip does neither: with
the **map + on-demand range** model, the new session sees every one of
1,117 messages as a single index line and fetches any message by number.
Dictionary tokens (`␟3␞`) look nothing like natural text — no collision
risk — and the round-trip is bit-exact (test suite: 210/210 messages,
bit-for-bit).

## Install

```bash
git clone https://github.com/Jilazem/Czip && cd Czip
./install.sh
```

Installs: `czip` CLI (`~/.local/bin`) + Hermes plugin (`/czip`,
`/cunzip`, `/cmap`, `/csearch` slash commands) + skill bridge + a
7-tool MCP server. After restarting Hermes, the slash commands appear
as real commands in new sessions.

## Commands

English aliases work everywhere (`pack` = Turkish `paketle`, etc.);
Turkish remains the native naming:

```
czip pack <session_id|last|longest> [--full] [--jev]  → pack + 6-char id
czip read <id|last>           → WITHOUT UNPACKING: index + recent + status
czip map <id|last>            → RAG map (~1.5k tokens; the cheap read)
czip search <id|last> "query" → search inside a pack, find message indices
czip range <id|last> <a-b>    → exact messages, tokens resolved
czip merge [auto|<id1> <id2>] [--jev] [--days=7]
czip undo [<file>]            → restore archived sessions
czip list                     → registered packs: id | date | title
```

Carry-over flow in a new session:

```
czip map a37tvc               # which messages hold what
czip search a37tvc "edge case"
czip range a37tvc 1040-1060   # only the part you need
```

## Features

- **Tokenization** — repeated blocks collapse into dictionary tokens;
  raw duplication never enters the pack (the secret behind 18×).
- **Lossless mode** — `--full`: every byte comes back (8.4×).
- **State detection** — `read` reports `awaiting_user_reply` plus the
  last message's role, so the new session resumes exactly where the old
  one stopped.
- **Privacy** — `state.db` is always opened via a READ-ONLY URI; packs
  are written only under `~/.hermes/session-packs`; nothing leaves the
  machine; the engine has no delete/corrupt capability.
- **Optional Jev gate** — `--jev` asks an external LLM
  (typesafe.ai System-One) in batches whether tool outputs above
  1,200 B are needed; outputs containing error hints are never
  questioned. **Off by default** — it sends sample content out, so it
  requires an explicit flag + API key, and silently falls back to
  static behavior on network failure.

## Components

| File | Role |
|---|---|
| `hkp.py` | **engine** — pure stdlib (Python 3.9+, 0 deps): pack/read/search, CLI, state.db RO reader |
| `czip` | terminal entry point (one-line wrapper) |
| `plugin/` | Hermes plugin — `/czip`, `/cunzip` slash commands |
| `mcp_server.py` | 7-tool MCP server (compress/open/guide/messages/…) |
| `skills/` | skill bridge — commands work even in old processes |
| `scripts/kapak.py` | Pillow script that renders the terminal cover |
| `tests/` | synthetic round-trip + CLI + range tests (4/4) |

## Format: HKP1

```
"HKP1" | meta_len (4B BE) | data_len (4B BE) | LZMA-9e(meta) | LZMA-9e(body)
```

Meta: title, dictionary, counters, format version, packing info.
Body: message record list (role, content, tool calls, reasoning,
timestamp) — resolved into readable text on demand.

## Tests

```bash
python3 tests/test_roundtrip.py   # 4/4: pack→read→range + stats
```

## Limits

- `smart` mode truncates tool outputs to 2000+2000 B head/tail slices
  (and reports it); use `--full` for a bit-exact archive.
- Pack files are plain-text LZMA — no encryption. Do not expose packs
  from sensitive sessions to anyone with disk access.
- The `state.db` schema varies with Hermes versions; the engine skips
  missing columns via PRAGMA.
- `--jev` is an external service (off by default); avoid it under
  data-protection policies.

## License

- **Individual / non-commercial use: free and open.**
- **Commercial use: separate license required.** Details: [LICENSE](LICENSE)

Versions before 2026-09-20 were released under MIT; those versions
remain MIT-licensed.
