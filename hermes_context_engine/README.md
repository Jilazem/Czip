# Hermes context engine and job/source search

The optional engine saves messages removed by Hermes compaction or tool-output
pruning as raw JSONL plus an HKP1 search copy. Storage failures return the original
context instead of claiming successful compaction. Native context size is still
finite. Creating a pack alone does not reset a running chat's context.

## Install explicitly

Keep the repository available at a stable path. With a Hermes version supporting
context-engine plugins, link the engine into the **Hermes code checkout**:

```sh
export CZIP_HOME=/absolute/path/to/Czip
mkdir -p /absolute/path/to/hermes-agent/plugins/context_engine
ln -s "$CZIP_HOME/hermes_context_engine/czip" /absolute/path/to/hermes-agent/plugins/context_engine/czip
```

Inspect any existing target and back it up before replacing it. A symlink keeps
the default CZIP_HOME resolution correct; a copied plugin needs CZIP_HOME set in
the actual service environment. The general `install.sh` installs CLI/slash-command
integration; it does **not** switch the context engine or restart services.

In the selected Hermes profile's `config.yaml`:

```yaml
context:
  engine: czip
czip_context:
  mode: hybrid  # native LLM summary plus archive; map skips the summary call
```

For a verified 262,144-token local route, an optional policy is:

```yaml
compression:
  threshold: 0.85
  threshold_tokens: 200000  # fallback upper cap; output reservation may lower it
czip_context:
  mode: map
  threshold_percent: 0.85
  threshold_tokens: 190000
```

The effective trigger is the smaller of the percentage-derived budget and its
absolute cap. Setting only the absolute number while retaining a 40% threshold
will still trigger early. The engine keeps its own lower cap even when the host
hot-reloads `compression.threshold_tokens`; small-window safety remains in the
native derivation. Config changes do not replace an old engine instance or reload
its Python class. Check actual activation on your desktop before assuming a new
mode/cap is live. `map` skips the summary LLM and relies on source retrieval; this
changes how the handoff is read, not the retention of the original messages.

Start a new session or use your Hermes version's supported reload mechanism.
Verify plugin discovery, active engine and a real compaction; a copied file is
not proof that an already-running desktop process loaded it.

## Paths and commands

Environment variables are read when the module is loaded:

| Variable | Default | Purpose |
| --- | --- | --- |
| CZIP_HOME | Repository root resolved through engine symlink | HKP and retrieval modules |
| HERMES_HOME | `~/.hermes` | Hermes profile root |
| CZIP_CTX_DIR | `$HERMES_HOME/czip/context` | Exact raw archives and packs |
| CZIP_HERMES_ROOT | HERMES_HOME | Read-only Kanban/archive sources |
| CZIP_SYSTEM_ROOT | `$CZIP_HERMES_ROOT/system` | `ceo-planlar` and `ders-defteri` folders |
| CZIP_JOB_INDEX | `$CZIP_HERMES_ROOT/runtime/czip-is-kaynagi/index.db` | Local SQLite index |
| CZIP_RCLONE | rclone on PATH | Optional names-only Drive listing |

```sh
python czip_cli.py find-index --home /path/to/profile --system /path/to/system --index /path/to/index.db
python czip_cli.py find 'yangın raporu' --case 2026/123 --index /path/to/index.db
# Explicit opt-in network operation: uses your configured rclone account.
python czip_cli.py find-drive --remote 'your-remote:your-folder'
```

On Windows use `czip.cmd`, or invoke `python czip_cli.py` directly. On POSIX use
`./czip` or the installed `czip` command. Drive refresh updates the names cache;
run `find-index` afterward to index it. Listing has depth 4 and reports incomplete
coverage; it does not read remote file bodies. No embeddings or external LLM are
used by this retrieval index.

The engine exposes `czip_search`, `czip_range`, `czip_map` for the current archive,
and `czip_find` for indexed jobs across sessions. An empty session archive directs
the agent to job search. Search returns source hashes, plan provenance and coverage
age; an indexed `done` status does not verify a delivered artifact. Missing results
are not proof that a task or report never existed. Read cited sources first.

The index combines Turkish folding, FTS5/BM25 and structured case/topic tags. It
reads native `kanban.db` tasks, latest runs and attachments, plan Markdown, verified
archive maps (`runtime/oturum-arsivcisi/archives/*/map.json` with receipt), and an
optional Drive names cache. Missing providers retain the last successful snapshot
and mark it stale. Existing source databases and transcripts are preserved.

Raw JSONL and HKP packs are unencrypted by default. The index redacts common token
patterns, which is not a guarantee of secret removal. Keep archives and indices
private; do not commit session data. OS locks serialize archive writers on Windows,
Mac and Linux. JSONL and HKP are separately replaced: raw JSONL is authoritative
after an interrupted pair update. Repeated user commands remain distinct; session
continuations retain a persistent archive alias. Range reads paginate exact raw
text with a checksum instead of stripping whitespace or silently truncating it.

## Validation

```sh
python -m unittest discover -s tests -v
python tests/test_roundtrip.py
# In a Hermes Python environment with its agent modules importable:
python hermes_context_engine/test_czip_engine.py
```

Portable tests exercise real HKP, SQLite, failure paths and independent-process
locking with a minimal Hermes superclass stub. They do not prove native Hermes
compatibility. The separate integration script uses the real ContextCompressor,
temporary archives and injected configuration; it makes no LLM/network calls and
does not change HERMES_HOME or live service configuration.
