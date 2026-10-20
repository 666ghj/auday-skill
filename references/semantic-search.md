# Local semantic search

Semantic search finds related meaning, including Chinese queries for English
records, English queries for Chinese records, and mixed Chinese-English queries.
Keyword search remains useful for exact wording, names, identifiers, and numbers.
Select the appropriate mode for the task; neither mode implies that a retrieved
statement is correct. Do not claim that SQLite alone supplies semantic embeddings.

## Local setup

For a new environment, use [first-run setup](first-run.md). Recommend this
capability once, respect a saved deferral, and install it when selected or explicitly
requested. The agent performs the preparation:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh setup --semantic
```

Setup prepares the private base environment when needed, downloads and verifies
the pinned model/runtime, saves configuration, and runs a synthetic verification.
The model download is 333.59 MB and the runtime archives are about 11.2 MB; the
base environment is about 40 MB when needed, with more space required after
extraction. The official runtime requires macOS 13.3 or later. Explain the
downloads and [Gemma terms](https://ai.google.dev/gemma/terms); an explicit request
to prepare semantic search does not need another installation confirmation.

The semantic query module itself uses Python's standard library, the configured
local model, and local llama.cpp. It does not download during indexing/search or
call an external inference service. The phone is needed to synchronize new text;
building and searching an already synchronized index work offline. A successful
setup check verifies the local capability, not a user's as-yet unsynchronized data.

Use the complete **EmbeddingGemma 300M ordinary Q8_0** model:

- Repository: [ggml-org/embeddinggemma-300M-GGUF](https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/tree/0f741b5a6585bd53aeb15cd1372c56f2a0f65e12).
- Revision: `0f741b5a6585bd53aeb15cd1372c56f2a0f65e12`.
- File: `embeddinggemma-300M-Q8_0.gguf`, 333,590,944 bytes.
- SHA-256: `b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63`.
- Runtime: [llama.cpp b10865](https://github.com/ggml-org/llama.cpp/releases/tag/b10865),
  commit `d4389a4dd920d24c9592f1dc3badbd69be23bd09`; retain the matching local
  `llama-server` executable and its libraries.
- Model use is subject to the model's [Gemma terms](https://ai.google.dev/gemma/terms).
  Weights are not redistributed in this skill.

A similarly named QAT file without `dense_2` and `dense_3` is not interchangeable.
Configuration and inference verify the exact complete-model hash. The implementation
uses mean pooling, both projection layers, 768 dimensions, L2 normalization, the
model's query/document prefixes, and CPU `--no-repack`. Runtime identity includes
the executable and adjacent dynamic libraries, because the executable may be a
small launcher.

Keep installed model/runtime assets in an ordinary local application-support
directory, independently of development checkouts. Setup manages those assets;
the configuration also accepts explicit local paths for an existing verified
installation. No other Auday checkout is required.

```text
/bin/sh <skill-root>/scripts/bootstrap.sh index
/bin/sh <skill-root>/scripts/bootstrap.sh index-status
/bin/sh <skill-root>/scripts/bootstrap.sh search "a decision described in different words" --mode semantic --limit 10
/bin/sh <skill-root>/scripts/bootstrap.sh search "exact identifier" --mode keyword
```

For an existing verified model/runtime, configure their paths instead of downloading
another copy:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh configure-semantic --model <local-model.gguf> --server <local-runtime/llama-server>
```

The standalone module exposes the same implementation for manually managed
environments that already provide a compatible `python3`. A bootstrap-managed
installation should use the commands above; its private Python does not create a
global `python3` command:

```text
python3 <skill-root>/scripts/semantic_search.py --root <profile-directory> status
python3 <skill-root>/scripts/semantic_search.py --root <profile-directory> index
python3 <skill-root>/scripts/semantic_search.py --root <profile-directory> search "query" --kind memory --date 2026-10 --limit 10
```

## What is stored

Within the selected profile:

| File | Contents |
| --- | --- |
| `library.sqlite` | The synchronized phone records; read-only input to indexing |
| `semantic-config.json` | Local model/runtime paths and their configuration |
| `semantic.sqlite` | Persistent chunks, source IDs, times, states, and FP16 vectors |

These are disk files, not an in-memory cache. Semantic search does not replace the
human-readable text export. Its result IDs can be read through the ordinary
`read` command to recover the complete source record.

Text is initially split into at most 900 characters with 100-character overlap.
The tokenizer checks the document prefix, shortened title, and text together.
Oversized blocks are split again until they fit 512 tokens. Text is not silently
truncated. Title-only records can be indexed. Date filters support year, month,
or day prefixes matching source timestamps; they do not reinterpret time zones.

Each 768-dimensional FP16 vector uses 1,536 bytes before SQLite, text, and metadata
overhead. The number of vectors depends on text length and tokenization, so one
record can require several vectors. Model weight size, index storage, and process
memory are separate measurements.

## Updating and searching

Indexing takes a consistent short read of the synchronized database and releases
that read transaction before running the model. It keeps only the retrieval fields
in memory, avoiding large nested source payloads.

An unchanged record reuses its vectors when the text, title, chunking policy,
model, and runtime identity match. Changed records are encoded again; removed
records disappear from the new index. A new SQLite file is validated and atomically
replaces the old generation only when the source still matches. An interrupted
build leaves the previous complete index intact. Model or runtime changes cannot
mix vectors from incompatible generations.

Before searching, the module checks the source snapshot and record digest.
Synchronizing an unchanged snapshot only refreshes its last-sync time and does not
invalidate the vectors. Changed content requires another `index` command.

An explicit request to refresh the user's Auday content means one `sync`, followed
by one `index` update when a configured index already exists. Finish that update
even if the phone reports additional arrivals after the captured snapshot. Do not
keep synchronizing or indexing until the live archive stops changing, and do not
download models or create a new semantic capability as a side effect of refresh.

Queries use cosine similarity over the persistent vectors, with optional kind/date
filters. A record appears once, using its highest-scoring chunk. Returned data
contains a short source excerpt, record ID, title, kind, timestamp, state, character
offsets, and similarity score. Similarity is a ranking signal, not a confidence
estimate, factual verification, or identity attribution. For an exact quotation,
read the source record and preserve its original wording.

Semantic queries return up to 100 records and do not support `--offset` pagination.
Narrow the query, date, or kind when more focused coverage is needed; keyword
pagination is a separate retrieval workflow.

The inference process lives only for the current operation. It uses an authenticated
Unix socket inside a private temporary directory, with no TCP listener. Model
requests and private text are not logged. Stage/count progress goes to stderr;
the final JSON result goes to stdout. Explain those stages to the user, including
whether a long first build is still encoding records or has completed.

## Verification

Normal tests use synthetic fixtures and deterministic injected vectors to check
persistence, filtering, stale-source rejection, identity mismatches, incremental
reuse, atomic failure, and vector validity. Those tests do not establish model
quality.

An optional real-model test uses eight new synthetic records and eight queries,
covering both cross-language directions, mixed Chinese-English, and same-language
paraphrases. It also checks long-text token budgets. Run it with existing local
assets; it never downloads them:

```sh
AUDAY_SEMANTIC_MODEL="<local-model.gguf>" \
AUDAY_SEMANTIC_SERVER="<local-runtime/llama-server>" \
python3 -m unittest discover -s tests -p 'test_semantic_search.py' -v
```

Passing this small integration fixture does not establish general multilingual
accuracy, accuracy on personal recordings, or iPhone performance.
