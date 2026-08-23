from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from agent_product.services.knowledge_index import (
    InMemoryLexicalKnowledgeIndex,
    KnowledgeIndex,
    KnowledgeIndexChunk,
    KnowledgeIndexQuery,
    KnowledgeIndexSyncResult,
)

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt"}
IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
}
INDEXED_STATUSES = {"active", "published"}
KNOWN_STATUSES = INDEXED_STATUSES | {"archived", "draft", "excluded"}
MAX_DOCUMENT_BYTES = 1_000_000
MAX_READ_LINES = 400
DEFAULT_LIBRARY_ID = "default"

CHUNK_POLICY_VERSION = 1
CHUNK_TARGET_TOKENS = 480
CHUNK_SOFT_MAX_TOKENS = 600
CHUNK_HARD_MAX_TOKENS = 800
CHUNK_MIN_TOKENS = 80
CHUNK_FORCED_OVERLAP_TOKENS = 80
PARENT_CONTEXT_TARGET_TOKENS = 1_200
PARENT_CONTEXT_MAX_TOKENS = 1_500

_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_TOKEN_ESTIMATE_PATTERN = re.compile(r"[\u3400-\u9fff]|[a-zA-Z0-9_]+(?:[./-][a-zA-Z0-9_]+)*|[^\s]")
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")


class KnowledgeError(ValueError):
    """Base exception for knowledge-store errors."""


class KnowledgeFormatError(KnowledgeError):
    """Raised when a knowledge document has invalid metadata."""


class KnowledgeDocumentNotFoundError(KnowledgeError):
    """Raised when a requested knowledge document is not indexed."""


class KnowledgeChunkNotFoundError(KnowledgeError):
    """Raised when a requested retrieval chunk is not indexed."""


class KnowledgeLibraryNotFoundError(KnowledgeError):
    """Raised when a requested knowledge library does not exist."""


class KnowledgeConflictError(KnowledgeError):
    """Raised when a document changed after the editor loaded it."""


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: str
    title: str
    summary: str
    tags: tuple[str, ...]
    status: str
    source: str
    content: str
    body: str
    body_start_line: int
    updated_at: str
    revision: str


@dataclass(frozen=True, slots=True)
class _KnowledgeChunk:
    document: KnowledgeDocument
    section: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    text: str
    token_count: int
    chunk_id: str = ""
    parent_chunk_id: str = ""
    context: str = ""
    context_start_line: int = 0
    context_end_line: int = 0
    policy_version: int = CHUNK_POLICY_VERSION
    mergeable: bool = True


@dataclass(frozen=True, slots=True)
class _KnowledgeElement:
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    text: str
    mergeable: bool = True


def _parse_tags(raw_value: str) -> tuple[str, ...]:
    value = raw_value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return tuple(sorted({item.strip().casefold() for item in parsed if item.strip()}))
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    tags = {
        item.strip().strip("\"'").casefold()
        for item in value.split(",")
        if item.strip().strip("\"'")
    }
    return tuple(sorted(tags))


def _parse_scalar(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value.strip("\"'")
        if isinstance(parsed, str):
            return parsed
    return value.strip("\"'")


def _valid_document_id(value: str) -> bool:
    if not value or len(value) > 160 or value.startswith("/") or ".." in value.split("/"):
        return False
    return all(character.isalnum() or character in "-_./" for character in value)


def _split_front_matter(text: str, source: str) -> tuple[dict[str, str], str, int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 1

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise KnowledgeFormatError(f"{source}: front matter is missing its closing ---")

    metadata: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:closing_index], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator or not key.strip():
            raise KnowledgeFormatError(
                f"{source}:{line_number}: metadata must use key: value syntax"
            )
        metadata[key.strip().casefold()] = _parse_scalar(value)

    body = "\n".join(lines[closing_index + 1 :])
    return metadata, body, closing_index + 2


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        match = _HEADING_PATTERN.match(line.strip())
        if match:
            return match.group(1).strip()
    return None


def _estimated_token_count(text: str) -> int:
    """Return a deterministic, provider-neutral token estimate for chunk limits."""
    count = 0
    for match in _TOKEN_ESTIMATE_PATTERN.finditer(text):
        value = match.group(0)
        if value.isascii() and any(character.isalnum() for character in value):
            count += max(1, (len(value) + 7) // 8)
        else:
            count += 1
    return count


def _prefix_end_for_tokens(text: str, token_limit: int) -> int:
    low = 1
    high = len(text)
    best = 1
    while low <= high:
        middle = (low + high) // 2
        if _estimated_token_count(text[:middle]) <= token_limit:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _prefer_natural_break(text: str, proposed_end: int) -> int:
    if proposed_end >= len(text):
        return len(text)
    minimum = max(1, proposed_end * 2 // 3)
    candidates = [
        text.rfind("\n", minimum, proposed_end),
        *(text.rfind(mark, minimum, proposed_end) for mark in "。！？；.!?;"),
        text.rfind(" ", minimum, proposed_end),
    ]
    boundary = max(candidates)
    return boundary + 1 if boundary >= minimum else proposed_end


def _overlap_start(text: str, end: int, token_limit: int) -> int:
    low = 0
    high = end
    best = end
    while low <= high:
        middle = (low + high) // 2
        if _estimated_token_count(text[middle:end]) <= token_limit:
            best = middle
            high = middle - 1
        else:
            low = middle + 1
    for candidate in range(best, min(end, best + 64)):
        if candidate == 0 or text[candidate - 1].isspace() or text[candidate - 1] in "。！？；.!?;":
            return candidate
    return best


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _line_for_offset(element: _KnowledgeElement, offset: int, *, end: bool = False) -> int:
    line = element.start_line + element.text[:offset].count("\n")
    if end and offset > 0 and element.text[offset - 1] == "\n":
        line -= 1
    return max(element.start_line, line)


def _compact_snippet(text: str, limit: int = 700) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _metadata_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize_document(
    *,
    document_id: str,
    title: str,
    summary: str,
    tags: list[str],
    status: str,
    body: str,
) -> str:
    tag_list = ", ".join(_metadata_value(tag) for tag in tags)
    normalized_body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return (
        "---\n"
        f"id: {_metadata_value(document_id)}\n"
        f"title: {_metadata_value(title)}\n"
        f"summary: {_metadata_value(summary)}\n"
        f"tags: [{tag_list}]\n"
        f"status: {status}\n"
        "---\n\n"
        f"{normalized_body}\n"
    )


class KnowledgeBase:
    """A small, local-first Markdown knowledge store with lexical retrieval."""

    def __init__(self, root: str | Path, *, index: KnowledgeIndex | None = None):
        self.root = Path(root).resolve()
        self.index = index or InMemoryLexicalKnowledgeIndex()
        self._cache_lock = RLock()
        self._document_cache: dict[Path, tuple[int, int, KnowledgeDocument]] = {}
        self._chunk_cache: dict[
            tuple[str, str, int], tuple[KnowledgeIndexChunk, ...]
        ] = {}
        self._last_sync_signature: tuple[tuple[str, str, str, str], ...] | None = None

    def _iter_paths(self) -> list[Path]:
        if not self.root.exists():
            return []

        paths: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if any(
                part.startswith(".") or part.casefold() in IGNORED_DIRECTORIES
                for part in relative.parts
            ):
                continue
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix.casefold() not in SUPPORTED_SUFFIXES
            ):
                continue
            paths.append(path)
        return paths

    def _load_document(self, path: Path) -> KnowledgeDocument:
        relative = path.relative_to(self.root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise KnowledgeFormatError(f"{relative}: document could not be read") from exc
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise KnowledgeFormatError(f"{relative}: document is larger than 1 MB")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeFormatError(f"{relative}: document must be UTF-8 text") from exc

        metadata, body, body_start_line = _split_front_matter(content, relative)
        default_id = Path(relative).with_suffix("").as_posix()
        document_id = metadata.get("id", default_id).strip()
        if not _valid_document_id(document_id):
            raise KnowledgeFormatError(f"{relative}: invalid document id {document_id!r}")

        status = metadata.get("status", "active").casefold()
        if status not in KNOWN_STATUSES:
            raise KnowledgeFormatError(
                f"{relative}: status must be one of {', '.join(sorted(KNOWN_STATUSES))}"
            )

        title = metadata.get("title") or _first_heading(body) or path.stem
        summary = metadata.get("summary", "")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        return KnowledgeDocument(
            id=document_id,
            title=title,
            summary=summary,
            tags=_parse_tags(metadata.get("tags", "")),
            status=status,
            source=relative,
            content=content,
            body=body,
            body_start_line=body_start_line,
            updated_at=updated_at,
            revision=sha256(raw).hexdigest(),
        )

    def _load_cached_document(self, path: Path) -> KnowledgeDocument:
        try:
            stat_result = path.stat()
        except OSError as exc:
            relative = path.relative_to(self.root).as_posix()
            raise KnowledgeFormatError(f"{relative}: document could not be read") from exc
        signature = (stat_result.st_mtime_ns, stat_result.st_size)
        with self._cache_lock:
            cached = self._document_cache.get(path)
            if cached is not None and cached[:2] == signature:
                return cached[2]
        document = self._load_document(path)
        with self._cache_lock:
            self._document_cache[path] = (*signature, document)
        return document

    def _documents(self, *, include_inactive: bool = False) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        seen_ids: set[str] = set()
        paths = self._iter_paths()
        active_paths = set(paths)
        with self._cache_lock:
            self._document_cache = {
                path: cached
                for path, cached in self._document_cache.items()
                if path in active_paths
            }
        for path in paths:
            try:
                document = self._load_cached_document(path)
            except KnowledgeFormatError as exc:
                logger.warning("Skipping invalid knowledge document: %s", exc)
                continue
            if not include_inactive and document.status not in INDEXED_STATUSES:
                continue
            if document.id in seen_ids:
                logger.warning("Skipping duplicate knowledge document id: %s", document.id)
                continue
            seen_ids.add(document.id)
            documents.append(document)
        return documents

    @staticmethod
    def chunk_policy() -> dict[str, Any]:
        return {
            "version": CHUNK_POLICY_VERSION,
            "strategy": "structure_first",
            "target_tokens": CHUNK_TARGET_TOKENS,
            "soft_max_tokens": CHUNK_SOFT_MAX_TOKENS,
            "hard_max_tokens": CHUNK_HARD_MAX_TOKENS,
            "min_tokens": CHUNK_MIN_TOKENS,
            "forced_split_overlap_tokens": CHUNK_FORCED_OVERLAP_TOKENS,
            "natural_boundary_overlap_tokens": 0,
            "parent_context_target_tokens": PARENT_CONTEXT_TARGET_TOKENS,
            "parent_context_max_tokens": PARENT_CONTEXT_MAX_TOKENS,
        }

    @staticmethod
    def _elements(document: KnowledgeDocument) -> list[_KnowledgeElement]:
        lines = document.body.splitlines()
        elements: list[_KnowledgeElement] = []
        heading_stack: list[str] = []
        heading_path = (document.title,)
        buffered_lines: list[str] = []
        buffered_start: int | None = None
        buffered_end: int | None = None

        def flush() -> None:
            nonlocal buffered_lines, buffered_start, buffered_end
            text = "\n".join(buffered_lines).strip()
            if text and buffered_start is not None and buffered_end is not None:
                elements.append(
                    _KnowledgeElement(
                        heading_path=heading_path,
                        start_line=buffered_start,
                        end_line=buffered_end,
                        text=text,
                    )
                )
            buffered_lines = []
            buffered_start = None
            buffered_end = None

        index = 0
        while index < len(lines):
            line = lines[index]
            absolute_line = document.body_start_line + index
            heading_match = _HEADING_PATTERN.match(line.strip())
            if heading_match:
                flush()
                level = len(line.lstrip()) - len(line.lstrip().lstrip("#"))
                heading_stack = heading_stack[: max(0, level - 1)]
                heading_stack.append(heading_match.group(1).strip())
                heading_path = tuple(heading_stack)
                index += 1
                continue

            fence_match = _FENCE_PATTERN.match(line)
            if fence_match:
                flush()
                fence = fence_match.group(1)
                start_index = index
                index += 1
                while index < len(lines):
                    candidate = lines[index].lstrip()
                    index += 1
                    if candidate.startswith(fence[0] * len(fence)):
                        break
                end_index = index - 1
                elements.append(
                    _KnowledgeElement(
                        heading_path=heading_path,
                        start_line=document.body_start_line + start_index,
                        end_line=document.body_start_line + end_index,
                        text="\n".join(lines[start_index:index]).strip(),
                        mergeable=False,
                    )
                )
                continue

            is_table = (
                index + 1 < len(lines)
                and "|" in line
                and _TABLE_SEPARATOR_PATTERN.match(lines[index + 1]) is not None
            )
            if is_table:
                flush()
                start_index = index
                index += 2
                while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                    index += 1
                end_index = index - 1
                elements.append(
                    _KnowledgeElement(
                        heading_path=heading_path,
                        start_line=document.body_start_line + start_index,
                        end_line=document.body_start_line + end_index,
                        text="\n".join(lines[start_index:index]).strip(),
                        mergeable=False,
                    )
                )
                continue

            if not line.strip():
                flush()
                index += 1
                continue
            if buffered_start is None:
                buffered_start = absolute_line
            buffered_end = absolute_line
            buffered_lines.append(line)
            index += 1
        flush()
        return elements

    @staticmethod
    def _make_chunk(
        document: KnowledgeDocument,
        *,
        heading_path: tuple[str, ...],
        start_line: int,
        end_line: int,
        text: str,
        mergeable: bool,
    ) -> _KnowledgeChunk:
        return _KnowledgeChunk(
            document=document,
            section=heading_path[-1] if heading_path else document.title,
            heading_path=heading_path or (document.title,),
            start_line=start_line,
            end_line=end_line,
            text=text.strip(),
            token_count=_estimated_token_count(text),
            mergeable=mergeable,
        )

    @classmethod
    def _split_oversized_element(
        cls,
        document: KnowledgeDocument,
        element: _KnowledgeElement,
    ) -> list[_KnowledgeChunk]:
        text = element.text
        chunks: list[_KnowledgeChunk] = []
        cursor = 0
        while cursor < len(text):
            remaining = text[cursor:]
            if _estimated_token_count(remaining) <= CHUNK_HARD_MAX_TOKENS:
                end = len(text)
            else:
                proposed = cursor + _prefix_end_for_tokens(
                    remaining,
                    CHUNK_TARGET_TOKENS,
                )
                end = cursor + _prefer_natural_break(remaining, proposed - cursor)
            actual_start, actual_end = _trimmed_span(text, cursor, end)
            if actual_end <= actual_start:
                break
            chunks.append(
                cls._make_chunk(
                    document,
                    heading_path=element.heading_path,
                    start_line=_line_for_offset(element, actual_start),
                    end_line=_line_for_offset(element, actual_end, end=True),
                    text=text[actual_start:actual_end],
                    mergeable=element.mergeable,
                )
            )
            if end >= len(text):
                break
            next_cursor = cursor + _overlap_start(
                remaining,
                end - cursor,
                CHUNK_FORCED_OVERLAP_TOKENS,
            )
            cursor = next_cursor if next_cursor > cursor else end
        return chunks

    @classmethod
    def _merge_chunks(
        cls,
        first: _KnowledgeChunk,
        second: _KnowledgeChunk,
    ) -> _KnowledgeChunk:
        return cls._make_chunk(
            first.document,
            heading_path=first.heading_path,
            start_line=min(first.start_line, second.start_line),
            end_line=max(first.end_line, second.end_line),
            text=f"{first.text.rstrip()}\n\n{second.text.lstrip()}",
            mergeable=first.mergeable and second.mergeable,
        )

    @staticmethod
    def _with_chunk_ids(chunks: list[_KnowledgeChunk]) -> list[_KnowledgeChunk]:
        identified: list[_KnowledgeChunk] = []
        for chunk in chunks:
            identity = "\0".join(
                (
                    chunk.document.id,
                    ">".join(chunk.heading_path),
                    str(chunk.start_line),
                    str(chunk.end_line),
                    chunk.text,
                )
            )
            chunk_id = f"kbch_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            identified.append(replace(chunk, chunk_id=chunk_id))
        return identified

    @staticmethod
    def _with_parent_context(chunks: list[_KnowledgeChunk]) -> list[_KnowledgeChunk]:
        contextualized: list[_KnowledgeChunk] = []
        for index, chunk in enumerate(chunks):
            group_start = index
            while group_start > 0 and chunks[group_start - 1].heading_path == chunk.heading_path:
                group_start -= 1
            group_end = index + 1
            while group_end < len(chunks) and chunks[group_end].heading_path == chunk.heading_path:
                group_end += 1

            selected_start = index
            selected_end = index + 1
            total_tokens = chunk.token_count
            while total_tokens < PARENT_CONTEXT_TARGET_TOKENS:
                added = False
                for candidate in (selected_start - 1, selected_end):
                    if candidate < group_start or candidate >= group_end:
                        continue
                    candidate_tokens = chunks[candidate].token_count
                    if total_tokens + candidate_tokens > PARENT_CONTEXT_MAX_TOKENS:
                        continue
                    if candidate < selected_start:
                        selected_start = candidate
                    else:
                        selected_end = candidate + 1
                    total_tokens += candidate_tokens
                    added = True
                    if total_tokens >= PARENT_CONTEXT_TARGET_TOKENS:
                        break
                if not added:
                    break

            parent_chunks = chunks[selected_start:selected_end]
            context = "\n\n".join(item.text for item in parent_chunks)
            context_start = min(item.start_line for item in parent_chunks)
            context_end = max(item.end_line for item in parent_chunks)
            parent_identity = "\0".join(item.chunk_id for item in parent_chunks)
            parent_chunk_id = f"kbctx_{sha256(parent_identity.encode('utf-8')).hexdigest()[:24]}"
            contextualized.append(
                replace(
                    chunk,
                    parent_chunk_id=parent_chunk_id,
                    context=context,
                    context_start_line=context_start,
                    context_end_line=context_end,
                )
            )
        return contextualized

    @classmethod
    def _chunks(cls, document: KnowledgeDocument) -> list[_KnowledgeChunk]:
        chunks: list[_KnowledgeChunk] = []
        buffered: list[_KnowledgeElement] = []
        buffered_tokens = 0

        def append(chunk: _KnowledgeChunk) -> None:
            if (
                chunk.token_count < CHUNK_MIN_TOKENS
                and chunk.mergeable
                and chunks
                and chunks[-1].mergeable
                and chunks[-1].heading_path == chunk.heading_path
                and chunks[-1].token_count + chunk.token_count <= CHUNK_HARD_MAX_TOKENS
            ):
                chunks[-1] = cls._merge_chunks(chunks[-1], chunk)
            else:
                chunks.append(chunk)

        def flush() -> None:
            nonlocal buffered, buffered_tokens
            if not buffered:
                return
            append(
                cls._make_chunk(
                    document,
                    heading_path=buffered[0].heading_path,
                    start_line=buffered[0].start_line,
                    end_line=buffered[-1].end_line,
                    text="\n\n".join(item.text for item in buffered),
                    mergeable=True,
                )
            )
            buffered = []
            buffered_tokens = 0

        for element in cls._elements(document):
            element_tokens = _estimated_token_count(element.text)
            if not element.mergeable or element_tokens > CHUNK_HARD_MAX_TOKENS:
                flush()
                for split_chunk in cls._split_oversized_element(document, element):
                    append(split_chunk)
                continue
            if buffered and buffered[0].heading_path != element.heading_path:
                flush()
            separator_tokens = 1 if buffered else 0
            combined_tokens = buffered_tokens + separator_tokens + element_tokens
            if buffered and (
                buffered_tokens >= CHUNK_TARGET_TOKENS or combined_tokens > CHUNK_SOFT_MAX_TOKENS
            ):
                flush()
            buffered.append(element)
            buffered_tokens += (1 if len(buffered) > 1 else 0) + element_tokens
        flush()
        return cls._with_parent_context(cls._with_chunk_ids(chunks))

    @staticmethod
    def _index_chunk(chunk: _KnowledgeChunk) -> KnowledgeIndexChunk:
        indexed = KnowledgeIndexChunk(
            chunk_id=chunk.chunk_id,
            parent_chunk_id=chunk.parent_chunk_id,
            document_id=chunk.document.id,
            title=chunk.document.title,
            summary=chunk.document.summary,
            source=chunk.document.source,
            source_uri=f"kb://{chunk.document.id}",
            heading_path=chunk.heading_path,
            tags=chunk.document.tags,
            content=chunk.text,
            context=chunk.context,
            line_start=chunk.start_line,
            line_end=chunk.end_line,
            context_line_start=chunk.context_start_line,
            context_line_end=chunk.context_end_line,
            token_count=chunk.token_count,
            policy_version=chunk.policy_version,
            updated_at=chunk.document.updated_at,
            revision=chunk.document.revision,
            content_hash="",
            library_id=DEFAULT_LIBRARY_ID,
            source_id=chunk.document.id,
        )
        indexed_payload = {
            "embedding_text": indexed.embedding_text,
            "parent_chunk_id": indexed.parent_chunk_id,
            "source": indexed.source,
            "source_uri": indexed.source_uri,
            "context": indexed.context,
            "line_start": indexed.line_start,
            "line_end": indexed.line_end,
            "context_line_start": indexed.context_line_start,
            "context_line_end": indexed.context_line_end,
            "token_count": indexed.token_count,
            "policy_version": indexed.policy_version,
            "updated_at": indexed.updated_at,
            "revision": indexed.revision,
        }
        return replace(
            indexed,
            content_hash=sha256(
                json.dumps(
                    indexed_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    def _sync_index(self) -> KnowledgeIndexSyncResult:
        documents = self._documents()
        signature = tuple(
            (document.id, document.revision, document.source, document.status)
            for document in sorted(documents, key=lambda item: item.id)
        )
        with self._cache_lock:
            if signature == self._last_sync_signature:
                return KnowledgeIndexSyncResult(
                    added=0,
                    updated=0,
                    removed=0,
                    total=self.index.status().chunk_count,
                )

            indexed_chunks: list[KnowledgeIndexChunk] = []
            live_keys: set[tuple[str, str, int]] = set()
            for document in documents:
                key = (document.id, document.revision, CHUNK_POLICY_VERSION)
                live_keys.add(key)
                cached_chunks = self._chunk_cache.get(key)
                if cached_chunks is None:
                    cached_chunks = tuple(
                        self._index_chunk(chunk) for chunk in self._chunks(document)
                    )
                    self._chunk_cache[key] = cached_chunks
                indexed_chunks.extend(cached_chunks)
            self._chunk_cache = {
                key: chunks
                for key, chunks in self._chunk_cache.items()
                if key in live_keys
            }
            result = self.index.sync(indexed_chunks)
            self._last_sync_signature = signature
            return result

    def index_status(self) -> dict[str, Any]:
        sync = self._sync_index()
        status = self.index.status()
        return {
            "backend": status.backend,
            "retrieval_modes": list(status.retrieval_modes),
            "chunk_count": status.chunk_count,
            "embedding_model": status.embedding_model,
            "embedding_dimensions": status.embedding_dimensions,
            "last_sync": {
                "added": sync.added,
                "updated": sync.updated,
                "removed": sync.removed,
                "total": sync.total,
            },
        }

    def list_libraries(self) -> list[dict[str, Any]]:
        active = self._documents()
        all_documents = self._documents(include_inactive=True)
        return [
            {
                "library_id": DEFAULT_LIBRARY_ID,
                "name": "本地知识库",
                "status": "active",
                "document_count": len(all_documents),
                "retrievable_document_count": len(active),
                "source_count": len(all_documents),
            }
        ]

    def list_sources(self, library_id: str) -> list[dict[str, Any]]:
        if library_id != DEFAULT_LIBRARY_ID:
            raise KnowledgeLibraryNotFoundError(
                f"Knowledge library {library_id!r} was not found"
            )
        sources: list[dict[str, Any]] = []
        for document in self._documents(include_inactive=True):
            if document.status in INDEXED_STATUSES:
                source_status = "ready"
            elif document.status in {"draft", "excluded"}:
                source_status = "pending"
            else:
                source_status = "archived"
            sources.append(
                {
                    "library_id": DEFAULT_LIBRARY_ID,
                    "source_id": document.id,
                    "kind": "managed-text",
                    "name": document.title,
                    "status": source_status,
                    "document_ids": [document.id],
                    "source": document.source,
                    "revision": document.revision,
                    "updated_at": document.updated_at,
                }
            )
        return sources

    @staticmethod
    def _summary(document: KnowledgeDocument) -> dict[str, Any]:
        return {
            "library_id": DEFAULT_LIBRARY_ID,
            "source_id": document.id,
            "document_id": document.id,
            "title": document.title,
            "summary": document.summary,
            "tags": list(document.tags),
            "status": document.status,
            "source": document.source,
            "updated_at": document.updated_at,
            "revision": document.revision,
        }

    def list_documents(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        return [
            self._summary(document)
            for document in self._documents(include_inactive=include_inactive)
        ]

    def get_document(self, document_id: str, *, include_inactive: bool = False) -> dict[str, Any]:
        document = next(
            (
                item
                for item in self._documents(include_inactive=include_inactive)
                if item.id == document_id
            ),
            None,
        )
        if document is None:
            raise KnowledgeDocumentNotFoundError(
                f"Knowledge document {document_id!r} was not found"
            )
        return {
            **self._summary(document),
            "body": document.body.strip(),
            "source_uri": f"kb://{document.id}",
        }

    def save_document(
        self,
        *,
        document_id: str,
        title: str,
        summary: str,
        tags: list[str],
        status: str,
        body: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        document_id = document_id.strip()
        title = title.strip()
        summary = summary.strip()
        status = status.casefold().strip()
        normalized_tags = sorted({tag.strip().casefold() for tag in tags if tag.strip()})
        if not _valid_document_id(document_id):
            raise KnowledgeFormatError(f"Invalid document id {document_id!r}")
        if not title:
            raise KnowledgeFormatError("Document title cannot be empty")
        if len(title) > 200 or len(summary) > 1_000 or len(body) > MAX_DOCUMENT_BYTES:
            raise KnowledgeFormatError("Document title, summary, or body is too large")
        if status not in KNOWN_STATUSES:
            raise KnowledgeFormatError(f"Status must be one of {', '.join(sorted(KNOWN_STATUSES))}")
        if len(normalized_tags) > 30 or any(
            len(tag) > 60 or "," in tag or "\n" in tag for tag in normalized_tags
        ):
            raise KnowledgeFormatError("Use at most 30 short tags without commas or newlines")

        existing = next(
            (item for item in self._documents(include_inactive=True) if item.id == document_id),
            None,
        )
        if existing is None:
            if expected_revision is not None:
                raise KnowledgeConflictError(
                    "The document no longer exists; reload the knowledge collection"
                )
            path = (self.root / f"{document_id}.md").resolve()
            action = "created"
        else:
            if expected_revision is None:
                raise KnowledgeConflictError(
                    "Provide the current revision before updating an existing document"
                )
            if expected_revision != existing.revision:
                raise KnowledgeConflictError(
                    "The document changed after it was loaded; reload it before saving"
                )
            path = (self.root / existing.source).resolve()
            action = "updated"

        if not path.is_relative_to(self.root):
            raise KnowledgeFormatError("The document path is outside the knowledge directory")
        if path.exists() and existing is None:
            raise KnowledgeConflictError("A file already exists at the requested document path")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise KnowledgeFormatError("The document path is not a regular file")

        content = _serialize_document(
            document_id=document_id,
            title=title,
            summary=summary,
            tags=normalized_tags,
            status=status,
            body=body,
        )
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise KnowledgeFormatError("Serialized document is larger than 1 MB")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.parent.resolve().is_relative_to(self.root):
                raise KnowledgeFormatError("The document path is outside the knowledge directory")
            temporary = path.with_name(f".{path.name}.knowledge-{uuid4().hex}.tmp")
            with temporary.open("x", encoding="utf-8", newline="") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except KnowledgeError:
            raise
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            raise KnowledgeFormatError("The knowledge document could not be saved") from exc

        with self._cache_lock:
            self._document_cache.pop(path, None)
            self._last_sync_signature = None
        saved = self._load_document(path)
        return {**self._summary(saved), "body": saved.body.strip(), "action": action}

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
        library_ids: list[str] | None = None,
        include_context: bool = False,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise KnowledgeError("Search query cannot be empty")
        if len(normalized_query) > 1_000:
            raise KnowledgeError("Search query is limited to 1,000 characters")
        limit = max(1, min(limit, 20))
        required_tags = frozenset(tag.strip().casefold() for tag in tags or [] if tag.strip())
        selected_libraries = frozenset(
            library_id.strip() for library_id in library_ids or [] if library_id.strip()
        )

        self._sync_index()
        matches = self.index.search(
            KnowledgeIndexQuery(
                text=normalized_query,
                limit=limit,
                required_tags=required_tags,
                library_ids=selected_libraries,
            )
        )
        if not matches:
            return {
                "query": normalized_query,
                "count": 0,
                "chunk_policy_version": CHUNK_POLICY_VERSION,
                "results": [],
            }
        results: list[dict[str, Any]] = []
        for match in matches:
            chunk = match.chunk
            citation = (
                f"{chunk.title} > {chunk.section} "
                f"({chunk.source}:L{chunk.line_start}-L{chunk.line_end})"
            )
            results.append(
                {
                    "library_id": chunk.library_id,
                    "source_id": chunk.source_id,
                    "document_id": chunk.document_id,
                    "title": chunk.title,
                    "section": chunk.section,
                    "heading_path": list(chunk.heading_path),
                    "chunk_id": chunk.chunk_id,
                    "parent_chunk_id": chunk.parent_chunk_id,
                    "chunk_policy_version": chunk.policy_version,
                    "token_count": chunk.token_count,
                    "snippet": _compact_snippet(chunk.content),
                    "context": chunk.context if include_context else None,
                    "context_line_start": (
                        chunk.context_line_start if include_context else None
                    ),
                    "context_line_end": chunk.context_line_end if include_context else None,
                    "source": chunk.source,
                    "source_uri": chunk.source_uri,
                    "line_start": chunk.line_start,
                    "line_end": chunk.line_end,
                    "score": match.score,
                    "lexical_score": match.lexical_score,
                    "vector_score": match.vector_score,
                    "rerank_score": match.rerank_score,
                    "tags": list(chunk.tags),
                    "updated_at": chunk.updated_at,
                    "citation": citation,
                    "context_citation": (
                        (
                            f"{chunk.title} > {chunk.section} "
                            f"({chunk.source}:L{chunk.context_line_start}"
                            f"-L{chunk.context_line_end})"
                        )
                        if include_context
                        else None
                    ),
                }
            )
        return {
            "query": normalized_query,
            "count": len(results),
            "chunk_policy_version": CHUNK_POLICY_VERSION,
            "results": results,
        }

    def read_context(self, chunk_id: str) -> dict[str, Any]:
        normalized = chunk_id.strip()
        if not normalized:
            raise KnowledgeError("Chunk ID cannot be empty")
        self._sync_index()
        chunk = self.index.get(normalized)
        if chunk is None:
            raise KnowledgeChunkNotFoundError(
                f"Knowledge chunk {chunk_id!r} was not found"
            )
        return {
            "library_id": chunk.library_id,
            "source_id": chunk.source_id,
            "document_id": chunk.document_id,
            "title": chunk.title,
            "section": chunk.section,
            "heading_path": list(chunk.heading_path),
            "chunk_id": chunk.chunk_id,
            "parent_chunk_id": chunk.parent_chunk_id,
            "context": chunk.context,
            "context_line_start": chunk.context_line_start,
            "context_line_end": chunk.context_line_end,
            "source": chunk.source,
            "source_uri": chunk.source_uri,
            "tags": list(chunk.tags),
            "updated_at": chunk.updated_at,
            "revision": chunk.revision,
            "citation": (
                f"{chunk.title} > {chunk.section} "
                f"({chunk.source}:L{chunk.context_line_start}-L{chunk.context_line_end})"
            ),
        }

    def read_document(
        self,
        document_id: str,
        *,
        start_line: int = 1,
        end_line: int = 240,
    ) -> dict[str, Any]:
        document = next((item for item in self._documents() if item.id == document_id), None)
        if document is None:
            raise KnowledgeDocumentNotFoundError(
                f"Knowledge document {document_id!r} was not found"
            )
        if start_line < 1 or end_line < start_line:
            raise KnowledgeError("Use a valid positive line range")
        if end_line - start_line + 1 > MAX_READ_LINES:
            raise KnowledgeError(f"A single read is limited to {MAX_READ_LINES} lines")

        lines = document.content.splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered_content = "\n".join(
            f"{line_number}: {line}" for line_number, line in enumerate(selected, start=start_line)
        )
        actual_end = min(end_line, len(lines))
        return {
            "document_id": document.id,
            "library_id": DEFAULT_LIBRARY_ID,
            "source_id": document.id,
            "title": document.title,
            "source": document.source,
            "source_uri": f"kb://{document.id}",
            "start_line": start_line,
            "end_line": actual_end,
            "total_lines": len(lines),
            "content": numbered_content,
            "citation": f"{document.title} ({document.source}:L{start_line}-L{actual_end})",
        }
