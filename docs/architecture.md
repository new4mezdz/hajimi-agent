# Architecture

## Current request flow

```text
Client
  -> Tauri desktop shell or browser client
  -> desktop JSONL IPC (no TCP listener) or browser HTTP development transport
  -> FastAPI ASGI validation, tenant and workspace boundary
  -> Conversation repository
  -> Pydantic AI agent
       -> model provider
       -> local knowledge retrieval with line-level citations
       -> tenant-scoped order, shipment and inventory projections
       -> registered web and code workspace tools
       -> deferred approval for every file write
  -> read-only Git review service
       -> one-time confirmation for commit and push
  -> optimistic history update
  -> streamed response
```

## Boundaries

- `api`: ASGI validation, authentication hooks and status-code mapping; reachable through desktop
  IPC without binding a port, or HTTP in browser/server development mode.
- `services`: Agent orchestration, Profile composition and conversation use cases.
- `capabilities`: Modular Prompt/tool packs selected by an Agent Profile.
- `knowledge/`: Version-controlled source for Profile-scoped, read-only Agent knowledge.
- `db`: Persistence models, session lifecycle and repositories.
- `schemas`: Stable public request and response contracts.
- `core`: Configuration, logging and cross-cutting concerns.
- `web/src-tauri`: Desktop lifecycle, native directory picker permissions and local runtime shell.

The desktop development client starts the repository's Python virtual environment as a child
process; production bundles the same engine as a PyInstaller sidecar. Tauri sends ASGI requests
over JSONL IPC on the child's standard input and receives response starts, body chunks, completion,
errors and cancellation events on standard output. No localhost port is opened. The client closes
the input stream for graceful shutdown and force-terminates only after a timeout. A user-selected
directory becomes the only readable and writable workspace root. Paths are resolved before every
operation, symlink and Windows junction redirects are rejected, common build and dependency
directories are skipped, and secret or credential filenames are blocked.

Reads and calculator calls are automatic inside that boundary. Every `create_file`, `apply_patch`
or compatibility `write_file` call is deferred until the user approves the exact server-persisted
tool call. Client-supplied tool arguments are discarded during approval resumption, preventing the
browser from changing a path or content after the preview. Creation uses exclusive file semantics
and never overwrites an existing directory entry. Patches replace one exact unique UTF-8 segment,
optionally validate a complete-read SHA-256, and preserve all other content. Whole-file replacement
requires the SHA-256 returned by a complete `read_file`; replacements use a same-directory temporary
file plus atomic replacement. Pending approvals are bound to a stable tenant/workspace identity;
new prompts and workspace/settings changes wait until the user approves or rejects the proposal.

Git review uses fixed Git subcommands and never accepts an arbitrary command from the model or
client. The selected workspace must be the exact repository root. Status, branch metadata,
per-file unified diffs and `git diff --check` results are read automatically, while secret and
ignored paths remain outside the review boundary. Commit and push are two-phase operations: a
short-lived, one-time intent is bound to the tenant, workspace, action and repository fingerprint.
The service validates that state again after the user confirms. Agent-created commits disable Git
hooks and reject configured clean filters; pushes reject unsafe remote helpers, repository-local
credential helpers and force-push behavior. Project tests remain `not_run` until a sandboxed command
runner can produce trustworthy results.

Pydantic AI messages remain stored as a native JSON compatibility projection, preserving tool
calls/results instead of flattening them. In parallel, `conversation_events` records append-only
conversation, turn, request snapshot, exact persisted message, approval and outcome facts. Request
snapshots pin Profile/composition, static prompt, dynamic date, tool policy and JSON schemas. A
version column still provides optimistic concurrency control so two requests cannot silently
overwrite the message projection.

The local knowledge service reads active Markdown and text documents, caches unchanged parsed/chunk
snapshots, and returns compact ranked child excerpts with stable IDs and exact source lines. Parent
context is loaded separately by chunk ID. The replaceable local default is a persistent incremental
SQLite FTS5 index; memory lexical remains available for tests. A Profile KnowledgeScope filters
search, context expansion and direct document reads by stable library id and required tags.
It deliberately exposes no Agent-driven write operation. The service boundary can later be backed
by hybrid/vector retrieval or exposed through MCP without changing the Agent-facing query contract.

Procedural knowledge is a separate Skill capability. The model sees only published, Profile-visible
names and descriptions, then loads one complete bounded Markdown workflow with `load_skill`. Skills
are human-managed and cannot widen tool permissions or replace factual knowledge citations.

The built-in support example uses relational operational truth: customers, products, inventory,
orders, order-item purchase snapshots, shipments, masked payments, inventory movements and
after-sales cases share the main SQLAlchemy database. Domain services add authenticated tenant and
customer predicates and expose public order/case numbers; internal primary keys and arbitrary SQL
stay unavailable to the model. Refund/replacement availability is deterministic, and the only
Agent write creates an approved after-sales case transaction.

## Agent specialization

The process hosts an `AgentRuntime` containing immutable, versioned Agent Profiles. A Profile selects
ordered Capability Packs, a permission policy, a Profile-scoped knowledge provider and UI features.
The built-in `general`, `knowledge`, `code` and `support` Profiles share the same model, conversation,
approval and IPC runtime but expose different tool catalogs and domain dependencies.

Each conversation stores its Profile id, version and manifest hash in `conversation_profiles`.
Resuming with a different Profile is rejected, as is reusing a version after its manifest changed.
The separate binding table lets existing SQLite databases adopt Profiles through `create_all` without
altering the legacy `conversations` table. See [`agent-platform.md`](agent-platform.md) for the
specialization contract and knowledge-scope design.

## Production roadmap

1. Replace header-based tenant identification with verified JWT/OIDC claims.
2. Add Alembic migrations and disable automatic table creation.
3. Add Redis-backed rate limiting and idempotency keys.
4. Run long-lived workflows through Temporal, DBOS or another durable executor.
5. Add OpenTelemetry traces, latency/cost metrics and redaction over the existing audit events.
6. Add model routing, budgets, evaluation datasets and prompt/tool snapshot versioning.
7. Sign the Tauri application, sidecar and NSIS installer for public distribution.
8. Add a sandboxed shell, project-test result recording and auditable Git event history.
