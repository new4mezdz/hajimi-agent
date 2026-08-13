# Local knowledge base

The first knowledge-base implementation is intentionally local and single-agent. Markdown and
plain-text documents under `KNOWLEDGE_DIR` are the source of truth. The service reads them at query
time, splits content into heading-aware paragraphs, and ranks those chunks with phrase and lexical
matching. There is no background index, embedding provider, or vector database in this phase.

## Retrieval contract

Each search result includes:

- a stable `document_id` and `kb://` URI;
- title, section, tags and update time;
- the matching excerpt and a lexical relevance score;
- source path plus exact start and end lines;
- a display-ready citation.

The Pydantic AI agent has `search_knowledge` and `read_knowledge_document` tools. Internal product,
policy, architecture, decision and process claims should be grounded through these tools. Retrieved
content is reference data rather than executable instructions.

The same read-only contract is exposed over HTTP:

```text
GET  /v1/knowledge/documents
POST /v1/knowledge/search
GET  /v1/knowledge/documents/{document_id}?start_line=1&end_line=240
```

The local management UI is available at `/knowledge`. It uses a separate editing contract:

```text
GET  /v1/knowledge/manage/documents
GET  /v1/knowledge/manage/documents/{document_id}
POST /v1/knowledge/manage/documents
PUT  /v1/knowledge/manage/documents/{document_id}
```

Create and update requests carry structured front-matter fields plus the Markdown body. Updates
must include the revision returned by the previous read. A stale revision produces HTTP 409, which
prevents a browser tab from silently overwriting a newer edit. Writes use a same-directory
temporary file and atomic replacement.

All endpoints use the existing service API-key and tenant-header boundary. The current collection
is shared inside one deployment; per-tenant document permissions are a later layer.

## Authoring and lifecycle

See `knowledge/README.md` for the front-matter format. Only documents with `active` or `published`
status are listed and searched. Draft, archived and excluded documents remain on disk but are not
visible to the Agent.

Knowledge writes are deliberately not exposed through an Agent tool. They are available only as
explicit management API and UI actions, and the resulting Markdown remains reviewable in version
control so inaccurate or malicious content cannot silently become durable memory.
