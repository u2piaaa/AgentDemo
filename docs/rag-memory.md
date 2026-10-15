# RAG and memory pipeline

The October 2026 upgrade keeps PostgreSQL/pgvector and local-first operation.
No schema migration or new vector database is required.

## Active retrieval stages

1. Paragraph-aware overlapping chunks preserve indentation and paragraph breaks.
   Character offsets refer to text with CRLF normalized to LF and outer whitespace
   removed. PDF offsets refer to extracted text, not PDF coordinates or page numbers.
2. An extractive Contextual Retrieval variant adds the document title and Markdown
   section lineage to embedding and lexical inputs. It does **not** generate a
   per-chunk model summary. Original chunk bodies remain unchanged for citations.
3. Semantic candidates and bounded multilingual substring terms are independently
   scoped to the user and conversation/global documents. SQL performs lexical
   filtering/ranking before LIMIT, including Chinese n-grams and exact identifiers.
4. Reciprocal rank fusion combines both channels; equal source scores share ranks.
   Relevance precedes the conversation scope tiebreaker. This is lexical ranking,
   not a claimed BM25 implementation. Exact pgvector search retains filtered recall.
5. Optional neural listwise reranking evaluates the fused shortlist. Local MMR
   then balances ranked relevance against character-shingle overlap and omits
   almost identical evidence. Local MMR works without a model service.
6. The inspector persists source, method, contextual lineage, normalized offsets,
   reranker/fallback state and diversity selection with the assistant message.
   Fusion/reranker scores are ranking signals, not correctness probabilities.

The standard candidate pool is 24 or six times the requested result count, capped
at 120; final results cap at 20. Reranking receives at most 24 candidates by default.
Slow embeddings have at most half the runtime retrieval deadline (and at most
four seconds); reranking has at most one quarter (and three seconds by default).
The overall runtime retrieval deadline remains the final bound.

## Optional listwise reranker

The default `RAG_RERANK_MODE=local` makes no additional network calls. To opt into
a Jina-compatible service, configure the backend environment:

```dotenv
RAG_RERANK_MODE=api
RAG_RERANK_URL=https://api.jina.ai/v1/rerank
RAG_RERANK_MODEL=jina-reranker-v3.5
RAG_RERANK_API_KEY=<your-service-key>
RAG_RERANK_TIMEOUT_SECONDS=3
RAG_RERANK_CANDIDATES=24
```

A self-hosted compatible endpoint can replace the URL/model; the key is optional
for a local endpoint. Enabling an external endpoint sends the query and selected
document snippets to that service. Keys belong in ignored local configuration.
The adapter bounds query/document sizes, response bytes and duration, does not
follow redirects or retry, and validates a complete permutation of candidate
indices with finite scores. It only reorders already scoped candidates. Failure
falls back to local RRF/MMR evidence and is visible in citation metadata.

The Jina v3.5 request/response contract is covered by deterministic HTTP tests and
a real local HTTP browser acceptance fixture. No paid Jina call, downloaded neural
weights, or vendor-quality benchmark is part of that acceptance. The default
local pipeline and database ranking are exercised directly.

Existing documents remain searchable. **Reindex document** enriches their stored
chunks without changing chunk IDs, bodies or citation positions. An embedding
outage preserves an existing vector index; a keyword-only document can still gain
extractive context. Newly uploaded documents record the chunker, contextualization
version, content hash, embedding model and indexing mode in document metadata.

## Incremental memory

Recent messages stay raw (`AGENT_MEMORY_MESSAGE_LIMIT`, default 12). Older messages
are compacted chronologically with a `(created_at, id, offset)` cursor stored in
conversation metadata. Each successful summary atomically advances the cursor.
Large messages are consumed in consecutive bounded parts; unread tails are not
dropped. The input budget defaults to 16,000 new characters plus at most 8,000
characters of existing summary, and output must be nonempty and at most 8,000
characters without internal tool protocol markup.

`FOR UPDATE SKIP LOCKED` serializes compaction per conversation across processes.
The entire operation has a 15-second default budget. Failure or cancellation rolls
back only compaction; the assistant answer was committed earlier. The settings are
`AGENT_MEMORY_TIMEOUT_SECONDS` and `AGENT_MEMORY_INPUT_MAX_CHARS`.

Disabling/deleting a summary retains its source boundary on the conversation.
Future summaries only process newly eligible history and never seed themselves
with a disabled summary. Legacy summaries without a cursor are upgraded lazily;
prior disable timestamps and explicit deletion boundaries prevent forgotten
history from being regenerated. Original messages remain in conversation history.

## Evidence and references

- [pgvector hybrid retrieval and RRF](https://github.com/pgvector/pgvector#hybrid-search)
- [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
  informs prefixing context; this implementation uses extractive headings instead
  of model-generated context and keeps multilingual lexical matching.
- [Jina Reranker v3.5](https://jina.ai/news/jina-reranker-v3-5-faster-listwise-reranking-hybrid-attention-self-distillation/)
  and [the current rerank API](https://jina.ai/reranker/) define the optional
  2026 listwise reranking integration.
- `tests/test_rag_memory_integration.py` runs against real PostgreSQL/pgvector:
  evidence beyond chunk 50, title-only relevance, hybrid ranking, scope isolation,
  reindex preservation, incremental/cancelled/concurrent/forgotten memory.
- `tests/test_retrieval_reranker.py` covers model transport bounds, invalid indices,
  timeout, cancellation, redirect refusal, fallback and MMR duplicate reduction.
