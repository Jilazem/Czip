# Optional local EmbeddingGemma 2 search

The optional resident service adds semantic candidates to Czip's existing FTS5
search. It is a text-only model on the same host; Hermes calls a small standard
library client rather than importing PyTorch into its own environment.
No session text is sent to an external API. The model stays loaded between queries.

## Install and start

Use Python 3.12 in a dedicated environment and install:

```sh
python -m pip install -r requirements-embedding.txt
```

Download the public model once, before enabling offline inference. Use the same
HF_HOME for download and the resident service. The pinned model is
`google/embeddinggemma-2` at `914f7f89142e33e77833254d9c9b90c3cef7303b`.
Its revision must match the vectors; a signature change rebuilds this separate
vector cache, never the original task/session databases.

```python
from huggingface_hub import snapshot_download
snapshot_download('google/embeddinggemma-2', revision='914f7f89142e33e77833254d9c9b90c3cef7303b')
```

Create a private random service token (at least 32 characters), readable only by
your OS user. Then start the service with your actual paths:

```sh
python embedding_service.py --sources /path/to/index.db --vectors /path/to/vectors.db --token-file /path/to/service.token --device mps --port 19127
```

`mps` targets Apple Silicon's Metal backend; `cpu` is available on other hosts.
The tested text-only configuration has 271,002,624 parameters. It uses float32;
float16 is unsuitable for this model. The processor still requires Pillow and
torchvision, although the vision/audio model encoders are disabled.

Use your OS service manager to keep this process running after login. Bind stays
at 127.0.0.1, and all endpoints require the private bearer token. `/health` reports
loading/ready, model revision, dimensions, indexing coverage and errors. `/index`
queues an incremental background refresh; a five-minute refresh also runs inside
the process. `/compare` supports small local retrieval-quality comparisons.
Health readiness and full index coverage are separate states.

On macOS, background indexing pauses while the OS reports warning or critical
memory pressure and resumes when pressure returns to normal. Critical pressure
also skips model queries so the client falls back to keyword retrieval. The MPS
allocator is capped at 3 GiB by default (`CZIP_MPS_BUDGET_BYTES` overrides this
allocator budget; it is not a total process RAM limit). Unused Metal buffers are
released after inference. `/health` reports pressure, indexing pause, allocated
and driver memory; no other application is stopped or reconfigured.

## Connect Czip

Create `$CZIP_HERMES_ROOT/runtime/czip-is-kaynagi/semantic.json`, or set
`CZIP_SEMANTIC_CONFIG` to another private config file:

```json
{
  "enabled": true,
  "url": "http://127.0.0.1:19127",
  "token_file": "/path/to/service.token",
  "timeout": 3
}
```

The existing `czip find` and `czip_find` routes now combine keyword ranking and
semantic ranking with reciprocal rank fusion. Exact case/kind filters apply at
both service and client boundaries. Arbitrary numeric queries never expand to
similar but numerically different cases. Returned vector references must match
the current source SHA; obsolete/deleted source vectors are rejected.

The service indexes only changed source chunks with original character offsets.
Long sources are split with overlap instead of dropping their trailing text.
512-dimensional normalized vectors live in a separate private SQLite database.
An interrupted source update retains the previous vectors; hashes ensure a stale
vector cannot stand in for a changed source. The client excludes remote URLs and
ignores proxy settings, so source queries remain on loopback.

If the model is loading, indexing coverage is partial, or the service is unavailable,
Czip keeps keyword results and reports semantic status/coverage. Set `enabled` to
false to disable semantic retrieval without deleting any source or vector data.
Similarity is a retrieval candidate, not evidence of factual correctness or file
delivery. Read the cited original source before accepting an answer.

Test Turkish paraphrases and domain queries against the keyword baseline before
activation. A small successful smoke test does not establish broad Turkish quality.
Do not publish private source indices, vectors, tokens or transcript packages.

References: [Google inference guide](https://ai.google.dev/gemma/docs/embeddinggemma/inference-embeddinggemma-with-sentence-transformers),
[model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2).
