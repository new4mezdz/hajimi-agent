# Architecture

## Current request flow

```text
Client
  -> Tauri desktop shell or browser client
  -> FastAPI authentication, tenant and workspace boundary
  -> Conversation repository
  -> Pydantic AI agent
       -> model provider
       -> registered web and read-only code workspace tools
  -> optimistic history update
  -> streamed response
```

## Boundaries

- `api`: HTTP validation, authentication hooks and status-code mapping.
- `services`: Agent orchestration and conversation use cases.
- `db`: Persistence models, session lifecycle and repositories.
- `schemas`: Stable public request and response contracts.
- `core`: Configuration, logging and cross-cutting concerns.
- `web/src-tauri`: Desktop lifecycle, native directory picker permissions and local runtime shell.

The desktop development client starts the repository's Python virtual environment as a child
process and stops it when the client exits. A user-selected directory becomes the only readable
workspace root. Paths are resolved before every read, symlink escapes are rejected, common build
and dependency directories are skipped, and secret or credential filenames are blocked.

Pydantic AI messages are stored as its native JSON representation. This preserves tool calls and
tool results instead of flattening history into user/assistant text. A version column provides
optimistic concurrency control so two requests cannot silently overwrite the same conversation.

## Production roadmap

1. Replace header-based tenant identification with verified JWT/OIDC claims.
2. Add Alembic migrations and disable automatic table creation.
3. Add Redis-backed rate limiting and idempotency keys.
4. Run long-lived workflows through Temporal, DBOS or another durable executor.
5. Add OpenTelemetry traces, metrics, redaction and audit events.
6. Add model routing, budgets, evaluation datasets and prompt versioning.
7. Package the Python runtime as a signed Tauri sidecar for installable desktop releases.
8. Add approval-gated patch, shell and Git tools with auditable event history.
