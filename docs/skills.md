# Procedural Skills

Skills are curated procedural knowledge, separate from the factual document knowledge base. The
model receives only the published, Profile-visible `name` and `description` catalog. It loads the
complete body with `load_skill(name)` only when the task matches, so unrelated workflows do not
occupy the context window.

## Storage and format

`SKILLS_DIR` defaults to `skills/`. Discovery is deliberately shallow and accepts either:

```text
skills/<name>/SKILL.md
skills/<name>.md
```

The canonical name is kebab-case and must match its directory or file name. A bundle may keep
larger references or templates beside `SKILL.md`; the loader returns its relative `resource_base`
without eagerly reading or enumerating those files.
Copy [`examples/skills/incident-response/SKILL.md`](../examples/skills/incident-response/SKILL.md)
into `skills/incident-response/SKILL.md` for a minimal working example.

The repository ships four published `support` Profile Skills:

| Skill | Routes |
|---|---|
| `order-delivery-status` | Natural-language order lookup and delivery estimate |
| `after-sales-resolution` | Delivered-item refund/replacement/manual-review options |
| `refund-exception-review` | Explicit exception request after automatic paths are unavailable |
| `delivery-exception-triage` | In-transit, delayed, or apparently missing shipment |

```markdown
---
name: incident-response
description: Diagnose production incidents and prepare a bounded recovery plan.
version: 1
status: published
profiles: [code]
tags: [operations, safety]
disable-model-invocation: false
user-invocable: true
---

# Incident response

1. Confirm impact.
2. Collect current evidence.
3. Propose the smallest safe recovery action.
```

Supported statuses are `draft`, `published`/`active`, `archived`, and `disabled`. Only published
or active, model-invocable Skills enter the model catalog. An empty `profiles` list makes a Skill
available to every Agent Profile that includes the `skills` Capability Pack; otherwise the list is
an allowlist. Complete Skill files are bounded by `SKILL_MAX_BYTES` and descriptions by
`SKILL_CATALOG_DESCRIPTION_MAX_LENGTH`.

## Runtime contract

The local provider rereads the selected file on every `load_skill` call, so a body-only edit is
visible without restarting the Agent. Invalid, oversized, symlinked, duplicate, draft, or
out-of-scope Skills fail closed. Skills are human-managed files; the Agent has no Skill write tool.

The read-only client/API surface is:

```text
GET /v1/skills?profile_id=code
GET /v1/skills/{name}?profile_id=code
```

Skill content is operating guidance but cannot override system safety, tool permissions, or the
direct user request. Product facts should remain in `knowledge/` and be grounded through the
document retrieval tools instead of being copied into a large Skill.

## Observable execution trace

`load_skill` remains an ordinary audited tool call. The Web client renders a dedicated Skill card
with pending/completed/failed state, name, description, version, source, revision and tags. After a
successful load, the user may expand the exact procedural Markdown returned to the model.

This is an explicit workflow trace, not hidden chain-of-thought. The UI may show which Skill was
selected, which business tools were called, their structured outcomes, and approvals; it must not
claim to reveal private model reasoning. Conversation events record the request-time Skill catalog
and emit `skill.loaded` with name/version/source/revision for reproducible review.
