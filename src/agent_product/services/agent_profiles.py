from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from agent_product.core.config import Settings
from agent_product.services.knowledge_provider import KnowledgeScope

_PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_PERMISSION_POLICIES = {
    "no-local-write",
    "knowledge-read-only",
    "workspace-read-only",
    "workspace-write-with-approval",
    "support-case-write-with-approval",
}


class AgentProfileError(ValueError):
    pass


class AgentProfileNotFoundError(AgentProfileError):
    pass


class AgentProfileVersionError(AgentProfileError):
    pass


@dataclass(frozen=True, slots=True)
class AgentProfile:
    id: str
    version: str
    display_name: str
    description: str
    capability_packs: tuple[str, ...]
    permission_policy: str
    ui_features: tuple[str, ...] = ()
    knowledge_scope: KnowledgeScope | None = None
    persona: str | None = None

    def __post_init__(self) -> None:
        if not _PROFILE_ID.fullmatch(self.id):
            raise AgentProfileError(f"Invalid Agent Profile id: {self.id!r}")
        if not self.version.strip():
            raise AgentProfileError("Agent Profile version must not be empty")
        if not all(isinstance(pack_id, str) and pack_id for pack_id in self.capability_packs):
            raise AgentProfileError("Capability Pack ids must be non-empty strings")
        if len(set(self.capability_packs)) != len(self.capability_packs):
            raise AgentProfileError(f"Agent Profile {self.id!r} repeats a capability pack")
        has_knowledge = "knowledge" in self.capability_packs
        if has_knowledge != (self.knowledge_scope is not None):
            raise AgentProfileError(
                f"Agent Profile {self.id!r} must pair the knowledge Capability Pack "
                "with one Knowledge Scope"
            )
        if "workspace-write" in self.capability_packs and "workspace-read" not in (
            self.capability_packs
        ):
            raise AgentProfileError("workspace-write requires workspace-read")
        if self.permission_policy not in _PERMISSION_POLICIES:
            raise AgentProfileError(
                f"Unknown permission policy: {self.permission_policy!r}"
            )
        write_enabled = "workspace-write" in self.capability_packs
        if write_enabled != (self.permission_policy == "workspace-write-with-approval"):
            raise AgentProfileError(
                "workspace-write must use the workspace-write-with-approval policy, "
                "and that policy cannot be declared without the write Capability Pack"
            )
        support_enabled = "support" in self.capability_packs
        if support_enabled != (
            self.permission_policy == "support-case-write-with-approval"
        ):
            raise AgentProfileError(
                "the support Capability Pack must use support-case-write-with-approval, "
                "and that policy cannot be declared without the support Pack"
            )

    @property
    def manifest_hash(self) -> str:
        payload = {
            "id": self.id,
            "version": self.version,
            "capability_packs": self.capability_packs,
            "permission_policy": self.permission_policy,
            "ui_features": self.ui_features,
            "knowledge_scope": (
                {
                    "scope_id": self.knowledge_scope.scope_id,
                    "required_tags": self.knowledge_scope.required_tags,
                    "library_ids": self.knowledge_scope.library_ids,
                }
                if self.knowledge_scope
                else None
            ),
            "persona": self.persona,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()


class AgentProfileRegistry:
    def __init__(self, profiles: tuple[AgentProfile, ...], *, default_id: str) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise AgentProfileError("Agent Profile ids must be unique")
        if default_id not in self._profiles:
            raise AgentProfileError(f"Default Agent Profile {default_id!r} is not registered")
        self.default_id = default_id

    def get(self, profile_id: str | None = None) -> AgentProfile:
        selected = profile_id or self.default_id
        try:
            return self._profiles[selected]
        except KeyError as exc:
            raise AgentProfileNotFoundError(
                f"Unknown Agent Profile {selected!r}"
            ) from exc

    def get_bound(self, profile_id: str, version: str) -> AgentProfile:
        profile = self.get(profile_id)
        if profile.version != version:
            raise AgentProfileVersionError(
                f"Conversation uses Agent Profile {profile_id!r} version {version!r}; "
                f"this runtime provides version {profile.version!r}"
            )
        return profile

    def list(self) -> tuple[AgentProfile, ...]:
        return tuple(self._profiles.values())


def _builtin_profiles(settings: Settings) -> tuple[AgentProfile, ...]:
    web = ("web",) if settings.web_search_enabled else ()
    knowledge = ("knowledge",) if settings.knowledge_enabled else ()
    skills = ("skills",) if settings.skills_enabled else ()
    write = ("workspace-write",) if settings.workspace_write_enabled else ()
    support = (
        ("support",)
        if settings.support_enabled and settings.knowledge_enabled
        else ()
    )
    all_knowledge = (
        KnowledgeScope(scope_id="all-active") if settings.knowledge_enabled else None
    )
    return (
        AgentProfile(
            id="general",
            version="1",
            display_name="通用助手",
            description="基础时间、计算和可选联网搜索，不读取本地知识或代码工作区。",
            capability_packs=("common",) + web,
            permission_policy="no-local-write",
            ui_features=("chat",),
        ),
        AgentProfile(
            id="knowledge",
            version="2",
            display_name="知识助手",
            description="检索已发布的本地知识并返回可验证引用。",
            capability_packs=("common",) + web + knowledge + skills,
            permission_policy="knowledge-read-only",
            ui_features=("chat", "knowledge"),
            knowledge_scope=all_knowledge,
        ),
        AgentProfile(
            id="code",
            version="2",
            display_name="代码工作区助手",
            description="读取代码和知识，提议经过人工批准的文件修改。",
            capability_packs=("common",)
            + web
            + knowledge
            + skills
            + ("workspace-read",)
            + write,
            permission_policy=(
                "workspace-write-with-approval"
                if settings.workspace_write_enabled
                else "workspace-read-only"
            ),
            ui_features=("chat", "workspace", "git", "knowledge"),
            knowledge_scope=all_knowledge,
        ),
        *(
            (
                AgentProfile(
                    id="support",
                    version="2",
                    display_name="客服 Agent",
                    description="联查订单、物流与库存，计算退款/换货方案并提议受审批的客服工单。",
                    capability_packs=("common", "knowledge") + skills + support,
                    permission_policy="support-case-write-with-approval",
                    ui_features=("chat", "knowledge", "support"),
                    knowledge_scope=KnowledgeScope(
                        scope_id="support-default",
                        required_tags=("support",),
                        library_ids=("default",),
                    ),
                    persona=(
                        "You are a customer-support agent working only with demo order data. "
                        "Verify orders, shipment and inventory with tools, consult support "
                        "knowledge, and use deterministic after-sales options. Never claim a "
                        "refund or replacement was executed. "
                        "When action is needed, propose an approved support case and clearly "
                        "state that it enters human review. Do not request payment credentials "
                        "or expose internal data.\n"
                        "你是客服 Agent。必须先用工具核验订单和退款资格，再引用客服知识。不得"
                        "声称已经执行退款；需要动作时只能提议创建经批准的人工工单。"
                    ),
                ),
            )
            if support
            else ()
        ),
    )


def build_builtin_profile_registry(settings: Settings) -> AgentProfileRegistry:
    return AgentProfileRegistry(
        _builtin_profiles(settings),
        default_id=settings.default_agent_profile,
    )


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AgentProfileError(f"{field} must be an array of strings")
    return tuple(value)


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgentProfileError(f"{field} must be a non-empty string")
    return value


def load_agent_profile(path: Path) -> AgentProfile:
    """Load one declarative Profile; code-bearing plugins are intentionally excluded."""
    try:
        if path.stat().st_size > 128_000:
            raise AgentProfileError("Profile file exceeds the 128 KB limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentProfileError(f"Could not load Agent Profile {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentProfileError(f"Agent Profile {path} must contain one JSON object")
    allowed = {
        "id",
        "version",
        "display_name",
        "description",
        "capability_packs",
        "permission_policy",
        "ui_features",
        "knowledge_scope",
        "persona",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AgentProfileError(
            f"Agent Profile {path} contains unknown field(s): {', '.join(unknown)}"
        )
    scope_payload = payload.get("knowledge_scope")
    scope: KnowledgeScope | None = None
    if scope_payload is not None:
        if not isinstance(scope_payload, dict):
            raise AgentProfileError("knowledge_scope must be an object or null")
        scope_unknown = sorted(
            set(scope_payload) - {"scope_id", "required_tags", "library_ids"}
        )
        if scope_unknown:
            raise AgentProfileError(
                "knowledge_scope contains unknown field(s): " + ", ".join(scope_unknown)
            )
        scope = KnowledgeScope(
            scope_id=_required_string(scope_payload, "scope_id"),
            required_tags=_string_tuple(scope_payload.get("required_tags"), "required_tags"),
            library_ids=_string_tuple(scope_payload.get("library_ids"), "library_ids"),
        )
    persona = payload.get("persona")
    if persona is not None and not isinstance(persona, str):
        raise AgentProfileError("persona must be a string or null")
    return AgentProfile(
        id=_required_string(payload, "id"),
        version=_required_string(payload, "version"),
        display_name=_required_string(payload, "display_name"),
        description=_required_string(payload, "description"),
        capability_packs=_string_tuple(payload.get("capability_packs"), "capability_packs"),
        permission_policy=_required_string(payload, "permission_policy"),
        ui_features=_string_tuple(payload.get("ui_features"), "ui_features"),
        knowledge_scope=scope,
        persona=persona,
    )


def build_profile_registry(settings: Settings) -> AgentProfileRegistry:
    profiles = list(_builtin_profiles(settings))
    if settings.agent_profile_dir:
        root = Path(settings.agent_profile_dir)
        if root.exists():
            if not root.is_dir():
                raise AgentProfileError(f"AGENT_PROFILE_DIR is not a directory: {root}")
            profiles.extend(
                load_agent_profile(path)
                for path in sorted(root.glob("*.json"))
                if not path.name.endswith(".example.json")
            )
    return AgentProfileRegistry(
        tuple(profiles),
        default_id=settings.default_agent_profile,
    )
