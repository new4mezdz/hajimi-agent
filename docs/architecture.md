# Architecture

## Current request flow

```text
Client
  -> FastAPI authentication and tenant boundary
  -> Conversation repository
  -> Pydantic AI agent
       -> model provider
       -> registered tools
  -> optimistic history update
  -> JSON response
```

## Boundaries

- `api`: HTTP validation, authentication hooks and status-code mapping.
- `services`: Agent orchestration and conversation use cases.
- `db`: Persistence models, session lifecycle and repositories.
- `schemas`: Stable public request and response contracts.
- `core`: Configuration, logging and cross-cutting concerns.

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
7. Add a frontend only after the first product workflow is selected.

