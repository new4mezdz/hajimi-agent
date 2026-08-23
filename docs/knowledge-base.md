# Local knowledge base

The first knowledge-base implementation is intentionally local and single-agent. Markdown and
plain-text documents under `KNOWLEDGE_DIR` are the source of truth. The local Agent runtime caches
unchanged document/chunk snapshots, incrementally synchronizes a persistent SQLite FTS5 index, and
ranks heading-aware chunks with phrase and lexical matching. There is no deployed knowledge service,
embedding provider, or vector database in this phase. The V2 client-local model is described in
[`knowledge-system-v2.md`](knowledge-system-v2.md).

## Retrieval contract

Each search result includes:

- a stable `document_id` and `kb://` URI;
- title, section, tags and update time;
- the matching excerpt and a lexical relevance score;
- source path plus exact start and end lines;
- a display-ready citation;
- child and parent chunk IDs, policy version and full heading path;
- a precise matched snippet; bounded parent context is loaded separately by `chunk_id`.

The Pydantic AI agent has `search_knowledge`, `read_knowledge_context`, and
`read_knowledge_document` tools. Search is compact by default; the model expands only promising
hits. Internal product, policy, architecture, decision and process claims should be grounded through
these tools. Retrieved content is reference data rather than executable instructions.

The same read-only contract is exposed to the client as versioned local commands:

```text
GET  /v1/knowledge/chunk-policy
GET  /v1/knowledge/index-status
GET  /v1/knowledge/documents
POST /v1/knowledge/search
GET  /v1/knowledge/chunks/{chunk_id}/context
GET  /v1/knowledge/documents/{document_id}?start_line=1&end_line=240
```

## Index and embedding boundary

`KnowledgeBase` sends normalized chunks through the `KnowledgeIndex` protocol rather than owning a
specific ranking implementation. An index backend synchronizes a full chunk snapshot incrementally,
searches with tags and per-document limits, reads an exact chunk by stable ID, and reports its
capabilities. SQLite FTS5 is the local product default; `InMemoryLexicalKnowledgeIndex` remains the
dependency-free test and ephemeral backend.

Every indexed chunk has a canonical `embedding_text` composed from title, complete heading path,
summary, tags and the precise child content. Its `content_hash` covers that canonical payload so a
future vector backend only embeds added or changed chunks. Search matches already reserve separate
`lexical_score`, `vector_score` and `rerank_score` fields; the Agent-facing search contract therefore
does not change when hybrid retrieval is enabled.

Local and hosted embedding implementations conform to `EmbeddingModel`: a stable model ID, vector
dimension, batched document embedding, and query embedding. The interface deliberately does not
select or bundle a model. Open-source sentence embedding models can be added as adapters without
changing the knowledge store, chunk policy, management UI or Agent tools.

## Chunk Policy V1

Chunking is structure-first and uses a deterministic, provider-neutral token estimate. A short,
coherent document or section remains one retrieval chunk. Markdown headings are hard semantic
boundaries, so content is never merged across sections. Paragraphs inside one section are combined
toward 480 tokens, with a soft maximum of 600 and a hard maximum of 800 tokens. Fenced code blocks
and Markdown tables remain standalone elements.

Fixed-length splitting is only used when one semantic element exceeds 800 tokens. Those forced
splits target 480 tokens and repeat up to 80 tokens from the previous child. Natural heading and
paragraph boundaries have zero overlap. Ordinary fragments below 80 tokens are merged with the
previous chunk when they share a section and the result remains within the hard maximum.

Each child has a stable `chunk_id`, complete `heading_path`, line range, token count and policy
version. Retrieval scores the precise child, while also returning a `parent_chunk_id` and adjacent
section context targeting 1,200 tokens with a 1,500-token hard maximum. This implements “small chunk
retrieval, larger context return” without changing the original Markdown source.

The local management UI is available at `/knowledge`. It uses a separate editing contract:

```text
GET  /v1/knowledge/manage/documents
GET  /v1/knowledge/manage/documents/{document_id}
POST /v1/knowledge/manage/documents
PUT  /v1/knowledge/manage/documents/{document_id}
```

Create and update requests carry structured front-matter fields plus the Markdown body. Updates
must include the revision returned by the previous read. A stale revision produces conflict status
409, which prevents a client view from silently overwriting a newer edit. Writes use a same-directory
temporary file and atomic replacement.

In the desktop build these command paths travel over JSONL IPC to the bundled local Agent runtime;
they do not bind a localhost port. HTTP transport remains available only for browser development and
automated testing. The current compatibility collection is local to one installed client.

## Authoring and lifecycle

See `knowledge/README.md` for the front-matter format. Only documents with `active` or `published`
status are listed and searched. Draft, archived and excluded documents remain on disk but are not
visible to the Agent.

Knowledge writes are deliberately not exposed through an Agent tool. They are available only as
explicit management API and UI actions, and the resulting Markdown remains reviewable in version
control so inaccurate or malicious content cannot silently become durable memory.
