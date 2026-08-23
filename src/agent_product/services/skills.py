from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PUBLISHED_STATUSES = {"active", "published"}
_KNOWN_STATUSES = _PUBLISHED_STATUSES | {"archived", "disabled", "draft"}


class SkillError(ValueError):
    """Base exception for invalid or unavailable Skills."""


class SkillFormatError(SkillError):
    """Raised when a Skill file does not satisfy the local contract."""


class SkillNotFoundError(SkillError):
    """Raised when a Skill is absent or outside the active scope."""


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str
    version: str
    status: str
    profiles: tuple[str, ...]
    tags: tuple[str, ...]
    model_invocable: bool
    user_invocable: bool
    source: str
    revision: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "status": self.status,
            "profiles": list(self.profiles),
            "tags": list(self.tags),
            "model_invocable": self.model_invocable,
            "user_invocable": self.user_invocable,
            "source": self.source,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    summary: SkillSummary
    content: str
    resource_base: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.summary.as_dict(),
            "content": self.content,
            "resource_base": self.resource_base,
        }


@runtime_checkable
class SkillProvider(Protocol):
    """Profile-scoped catalog and on-demand Skill loader."""

    def list(self) -> tuple[SkillSummary, ...]: ...

    def get(self, name: str) -> SkillDefinition: ...


def _parse_scalar(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped.strip("\"'")
        if isinstance(parsed, str):
            return parsed
    return stripped.strip("\"'")


def _parse_list(value: str) -> tuple[str, ...]:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return tuple(item.strip() for item in parsed if item.strip())
        stripped = stripped[1:-1]
    return tuple(
        item.strip().strip("\"'")
        for item in stripped.split(",")
        if item.strip().strip("\"'")
    )


def _parse_bool(value: str, *, default: bool) -> bool:
    if not value:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SkillFormatError(f"Invalid boolean value {value!r}")


def _split_front_matter(text: str, source: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillFormatError(f"{source}: Skill metadata must start with ---")
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise SkillFormatError(f"{source}: Skill metadata is missing its closing ---")
    metadata: dict[str, str] = {}
    metadata_lines = lines[1:closing]
    index = 0
    while index < len(metadata_lines):
        line = metadata_lines[index]
        line_number = index + 2
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        key, separator, value = stripped.partition(":")
        if not separator or not key.strip():
            raise SkillFormatError(
                f"{source}:{line_number}: metadata must use key: value syntax"
            )
        normalized_value = value.strip()
        if normalized_value in {">", ">-", "|", "|-"}:
            continuation: list[str] = []
            index += 1
            while index < len(metadata_lines):
                candidate = metadata_lines[index]
                if candidate.strip() and not candidate[:1].isspace():
                    break
                continuation.append(candidate.strip())
                index += 1
            separator_text = "\n" if normalized_value.startswith("|") else " "
            normalized_value = separator_text.join(continuation).strip()
        else:
            index += 1
        metadata[key.strip().casefold()] = normalized_value
    return metadata, "\n".join(lines[closing + 1 :]).strip()


class LocalSkillRegistry:
    """Local Markdown Skill provider with summary discovery and fresh body reads."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 64_000,
        description_max_length: int = 500,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_bytes
        self.description_max_length = description_max_length
        if max_bytes <= 0:
            raise ValueError("Skill max_bytes must be positive")
        if description_max_length < 3:
            raise ValueError("Skill description_max_length must be at least 3")

    def _paths(self) -> list[Path]:
        if not self.root.exists() or not self.root.is_dir():
            return []
        paths: list[Path] = []
        for entry in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_file() and entry.suffix.casefold() == ".md":
                paths.append(entry)
                continue
            if entry.is_dir():
                candidate = entry / "SKILL.md"
                if candidate.is_file() and not candidate.is_symlink():
                    paths.append(candidate)
        return paths

    def _load(self, path: Path) -> SkillDefinition:
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root) or path.is_symlink():
                raise SkillFormatError("Skill path is outside the configured directory")
            raw = path.read_bytes()
        except OSError as exc:
            raise SkillFormatError(f"{path.name}: Skill could not be read") from exc
        if len(raw) > self.max_bytes:
            raise SkillFormatError(f"{path.name}: Skill exceeds the configured size limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillFormatError(f"{path.name}: Skill must be UTF-8 text") from exc

        source = path.relative_to(self.root).as_posix()
        metadata, body = _split_front_matter(text, source)
        fallback_name = path.parent.name if path.name == "SKILL.md" else path.stem
        name = _parse_scalar(metadata.get("name", fallback_name)).casefold()
        if not _SKILL_NAME.fullmatch(name):
            raise SkillFormatError(f"{source}: invalid Skill name {name!r}")
        if name != fallback_name.casefold():
            raise SkillFormatError(
                f"{source}: Skill name must match its file or directory name"
            )
        description = _parse_scalar(metadata.get("description", ""))
        if not description:
            raise SkillFormatError(f"{source}: description is required")
        if len(description) > self.description_max_length:
            raise SkillFormatError(
                f"{source}: description exceeds {self.description_max_length} characters"
            )
        status = _parse_scalar(metadata.get("status", "published")).casefold()
        if status not in _KNOWN_STATUSES:
            raise SkillFormatError(
                f"{source}: status must be one of {', '.join(sorted(_KNOWN_STATUSES))}"
            )
        if not body:
            raise SkillFormatError(f"{source}: Skill instructions cannot be empty")

        revision = sha256(raw).hexdigest()
        summary = SkillSummary(
            name=name,
            description=description,
            version=_parse_scalar(metadata.get("version", revision[:12])),
            status=status,
            profiles=tuple(
                sorted({item.casefold() for item in _parse_list(metadata.get("profiles", ""))})
            ),
            tags=tuple(
                sorted({item.casefold() for item in _parse_list(metadata.get("tags", ""))})
            ),
            model_invocable=not _parse_bool(
                metadata.get("disable-model-invocation", ""), default=False
            ),
            user_invocable=_parse_bool(
                metadata.get("user-invocable", ""), default=True
            ),
            source=source,
            revision=revision,
        )
        resource_base = source.rsplit("/", 1)[0] if "/" in source else None
        return SkillDefinition(summary=summary, content=body, resource_base=resource_base)

    def _definitions(self) -> dict[str, SkillDefinition]:
        definitions: dict[str, SkillDefinition] = {}
        for path in self._paths():
            try:
                definition = self._load(path)
            except SkillFormatError as exc:
                logger.warning("Skipping invalid Skill: %s", exc)
                continue
            if definition.summary.name in definitions:
                logger.warning("Skipping duplicate Skill name: %s", definition.summary.name)
                continue
            definitions[definition.summary.name] = definition
        return definitions

    def scoped(self, profile_id: str) -> ScopedSkillProvider:
        return ScopedSkillProvider(self, profile_id)

    def list_for_profile(self, profile_id: str) -> tuple[SkillSummary, ...]:
        return tuple(
            definition.summary
            for definition in self._definitions().values()
            if definition.summary.status in _PUBLISHED_STATUSES
            and definition.summary.model_invocable
            and (
                not definition.summary.profiles
                or profile_id.casefold() in definition.summary.profiles
            )
        )

    def get_for_profile(self, name: str, profile_id: str) -> SkillDefinition:
        normalized = name.strip().casefold()
        if not _SKILL_NAME.fullmatch(normalized):
            raise SkillNotFoundError(f"Unknown Skill {name!r}")
        definition = self._definitions().get(normalized)
        if (
            definition is None
            or definition.summary.status not in _PUBLISHED_STATUSES
            or not definition.summary.model_invocable
            or (
                definition.summary.profiles
                and profile_id.casefold() not in definition.summary.profiles
            )
        ):
            raise SkillNotFoundError(f"Skill {name!r} is not available in this Agent Profile")
        return definition


class ScopedSkillProvider:
    def __init__(self, registry: LocalSkillRegistry, profile_id: str) -> None:
        self.registry = registry
        self.profile_id = profile_id

    def list(self) -> tuple[SkillSummary, ...]:
        return self.registry.list_for_profile(self.profile_id)

    def get(self, name: str) -> SkillDefinition:
        return self.registry.get_for_profile(name, self.profile_id)
