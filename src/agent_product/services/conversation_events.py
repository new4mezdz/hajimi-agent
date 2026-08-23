from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic_ai import DeferredToolRequests
from pydantic_core import to_jsonable_python

from agent_product.core.config import Settings
from agent_product.services.agent_runtime import AgentRegistration


def request_snapshot(
    turn_id: str,
    registration: AgentRegistration,
    settings: Settings,
) -> dict[str, Any]:
    scope = registration.profile.knowledge_scope
    return {
        "turn_id": turn_id,
        "profile": {
            "id": registration.profile.id,
            "version": registration.profile.version,
            "manifest_hash": registration.profile.manifest_hash,
            "composition_hash": registration.composition_hash,
        },
        "model": settings.ai_model,
        "static_system_prompt": registration.static_system_prompt,
        "prompt_hash": registration.prompt_hash,
        "dynamic_context": {
            "current_date": datetime.now(UTC).date().isoformat(),
        },
        "declared_capability_packs": list(registration.profile.capability_packs),
        "active_capability_packs": list(registration.active_capability_packs),
        "tools": [policy.as_dict() for policy in registration.tool_catalog],
        "tool_schemas": list(registration.tool_schemas),
        "tool_schema_hash": registration.tool_schema_hash,
        "permission_policy": registration.profile.permission_policy,
        "knowledge_scope": (
            {
                "scope_id": scope.scope_id,
                "required_tags": list(scope.required_tags),
                "library_ids": list(scope.library_ids),
            }
            if scope
            else None
        ),
    }


def completed_run_events(
    turn_id: str,
    result: Any,
    *,
    duration_ms: int | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for message in result.new_messages():
        serialized_message = to_jsonable_python(message)
        events.append(
            (
                "message.persisted",
                {
                    "turn_id": turn_id,
                    "message": serialized_message,
                },
            )
        )
        if isinstance(serialized_message, dict):
            for part in serialized_message.get("parts", ()):
                if not isinstance(part, dict) or part.get("tool_name") != "load_skill":
                    continue
                content = part.get("content")
                if not isinstance(content, dict) or not isinstance(content.get("name"), str):
                    continue
                events.append(
                    (
                        "skill.loaded",
                        {
                            "turn_id": turn_id,
                            "name": content["name"],
                            "version": content.get("version"),
                            "source": content.get("source"),
                            "revision": content.get("revision"),
                        },
                    )
                )
    output = result.output
    if isinstance(output, DeferredToolRequests):
        events.append(
            (
                "approval.requested",
                {
                    "turn_id": turn_id,
                    "requests": [
                        {
                            "tool_call_id": request.tool_call_id,
                            "tool_name": request.tool_name,
                        }
                        for request in output.approvals
                    ],
                },
            )
        )
    usage = result.usage
    events.append(
        (
            "turn.completed",
            {
                "turn_id": turn_id,
                "usage": {
                    "requests": getattr(usage, "requests", 0) or 0,
                    "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                },
                "awaiting_approval": isinstance(output, DeferredToolRequests),
                "duration_ms": duration_ms,
            },
        )
    )
    return events
