# Architecture

## Current request flow

```text
Client
  -> Tauri desktop shell or browser client
  -> FastAPI authentication, tenant and workspace boundary
  -> Conversation repository
  -> Pydantic AI agent
       -> model provider
       -> local knowledge retrieval with line-level citations
       -> registered web and code workspace tools
       -> deferred approval for every file write
  -> read-only Git review service
       -> one-time confirmation for commit and push
  -> optimistic history update
  -> streamed response
```

## Boundaries

- `api`: HTTP validation, authentication hooks and status-code mapping.
- `services`: Agent orchestration and conversation use cases.
- `knowledge/`: Version-controlled, read-only knowledge sources for the single Agent.
- `db`: Persistence models, session lifecycle and repositories.
- `schemas`: Stable public request and response contracts.
- `core`: Configuration, logging and cross-cutting concerns.
- `web/src-tauri`: Desktop lifecycle, native directory picker permissions and local runtime shell.

The desktop development client starts the repository's Python virtual environment as a child
process and stops it when the client exits. A user-selected directory becomes the only readable
and writable workspace root. Paths are resolved before every operation, symlink escapes are
rejected, common build and dependency directories are skipped, and secret or credential filenames
are blocked.

Reads are automatic inside that boundary. Every `write_file` call is deferred until the user
approves the exact server-persisted tool call. Client-supplied tool arguments are discarded during
approval resumption, preventing the browser from changing a path or content after the preview.
Existing files require the SHA-256 returned by `read_file`, and writes use a same-directory
temporary file plus atomic replacement.

Git review uses fixed Git subcommands and never accepts an arbitrary command from the model or
client. The selected workspace must be the exact repository root. Status, branch metadata,
per-file unified diffs and `git diff --check` results are read automatically, while secret and
ignored paths remain outside the review boundary. Commit and push are two-phase operations: a
short-lived, one-time intent is bound to the tenant, workspace, action and repository fingerprint.
The service validates that state again after the user confirms. Agent-created commits disable Git
hooks and reject configured clean filters; pushes reject unsafe remote helpers, repository-local
credential helpers and force-push behavior. Project tests remain `not_run` until a sandboxed command
runner can produce trustworthy results.

Pydantic AI messages are stored as its native JSON representation. This preserves tool calls and
tool results instead of flattening history into user/assistant text. A version column provides
optimistic concurrency control so two requests cannot silently overwrite the same conversation.

The local knowledge service reads active Markdown and text documents at query time, splits them by
heading and paragraph, and returns ranked excerpts with stable document IDs and exact source lines.
It deliberately exposes no Agent-driven write operation. The service boundary can later be backed
by hybrid/vector retrieval or exposed through MCP without changing the Agent-facing query contract.

## Production roadmap

1. Replace header-based tenant identification with verified JWT/OIDC claims.
2. Add Alembic migrations and disable automatic table creation.
3. Add Redis-backed rate limiting and idempotency keys.
4. Run long-lived workflows through Temporal, DBOS or another durable executor.
5. Add OpenTelemetry traces, metrics, redaction and audit events.
6. Add model routing, budgets, evaluation datasets and prompt versioning.
7. Package the Python runtime as a signed Tauri sidecar for installable desktop releases.
8. Add a sandboxed shell, project-test result recording and auditable Git event history.
