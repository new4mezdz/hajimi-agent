from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

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

_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*|[\u3400-\u9fff]+")


class KnowledgeError(ValueError):
    """Base exception for knowledge-store errors."""


class KnowledgeFormatError(KnowledgeError):
    """Raised when a knowledge document has invalid metadata."""


class KnowledgeDocumentNotFoundError(KnowledgeError):
    """Raised when a requested knowledge document is not indexed."""


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
    start_line: int
    end_line: int
    text: str


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


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text.casefold()):
        token = match.group(0)
        if token.isascii():
            tokens.append(token)
            continue
        tokens.append(token)
        if len(token) > 1:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        if len(token) > 2:
            tokens.extend(token[index : index + 3] for index in range(len(token) - 2))
    return tokens


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

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

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

    def _documents(self, *, include_inactive: bool = False) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        seen_ids: set[str] = set()
        for path in self._iter_paths():
            try:
                document = self._load_document(path)
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
    def _chunks(document: KnowledgeDocument) -> list[_KnowledgeChunk]:
        chunks: list[_KnowledgeChunk] = []
        section = document.title
        buffered_lines: list[str] = []
        start_line: int | None = None
        end_line: int | None = None

        def flush() -> None:
            nonlocal buffered_lines, start_line, end_line
            text = "\n".join(buffered_lines).strip()
            if text and start_line is not None and end_line is not None:
                chunks.append(
                    _KnowledgeChunk(
                        document=document,
                        section=section,
                        start_line=start_line,
                        end_line=end_line,
                        text=text,
                    )
                )
            buffered_lines = []
            start_line = None
            end_line = None

        for offset, line in enumerate(document.body.splitlines()):
            absolute_line = document.body_start_line + offset
            heading_match = _HEADING_PATTERN.match(line.strip())
            if heading_match:
                flush()
                section = heading_match.group(1).strip()
                continue
            if not line.strip():
                flush()
                continue
            if start_line is None:
                start_line = absolute_line
            end_line = absolute_line
            buffered_lines.append(line)
        flush()
        return chunks

    @staticmethod
    def _summary(document: KnowledgeDocument) -> dict[str, Any]:
        return {
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
        normalized_tags = sorted(
            {
                tag.strip().casefold()
                for tag in tags
                if tag.strip()
            }
        )
        if not _valid_document_id(document_id):
            raise KnowledgeFormatError(f"Invalid document id {document_id!r}")
        if not title:
            raise KnowledgeFormatError("Document title cannot be empty")
        if len(title) > 200 or len(summary) > 1_000 or len(body) > MAX_DOCUMENT_BYTES:
            raise KnowledgeFormatError("Document title, summary, or body is too large")
        if status not in KNOWN_STATUSES:
            raise KnowledgeFormatError(
                f"Status must be one of {', '.join(sorted(KNOWN_STATUSES))}"
            )
        if len(normalized_tags) > 30 or any(
            len(tag) > 60 or "," in tag or "\n" in tag for tag in normalized_tags
        ):
            raise KnowledgeFormatError("Use at most 30 short tags without commas or newlines")

        existing = next(
            (
                item
                for item in self._documents(include_inactive=True)
                if item.id == document_id
            ),
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
                raise KnowledgeFormatError(
                    "The document path is outside the knowledge directory"
                )
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

        saved = self._load_document(path)
        return {**self._summary(saved), "body": saved.body.strip(), "action": action}

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise KnowledgeError("Search query cannot be empty")
        if len(normalized_query) > 1_000:
            raise KnowledgeError("Search query is limited to 1,000 characters")
        limit = max(1, min(limit, 20))
        required_tags = {tag.strip().casefold() for tag in tags or [] if tag.strip()}

        chunks = [
            chunk
            for document in self._documents()
            if not required_tags or required_tags.issubset(set(document.tags))
            for chunk in self._chunks(document)
        ]
        query_tokens = Counter(_tokenize(normalized_query))
        if not query_tokens or not chunks:
            return {"query": normalized_query, "count": 0, "results": []}

        chunk_tokens: list[Counter[str]] = []
        document_frequencies: Counter[str] = Counter()
        for chunk in chunks:
            searchable = " ".join(
                (
                    chunk.document.title,
                    chunk.document.summary,
                    chunk.section,
                    " ".join(chunk.document.tags),
                    chunk.text,
                )
            )
            counts = Counter(_tokenize(searchable))
            chunk_tokens.append(counts)
            document_frequencies.update(set(counts) & set(query_tokens))

        scored: list[tuple[float, _KnowledgeChunk]] = []
        total_chunks = len(chunks)
        query_casefold = normalized_query.casefold()
        for chunk, counts in zip(chunks, chunk_tokens, strict=True):
            score = 0.0
            for token, query_count in query_tokens.items():
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                inverse_frequency = math.log(
                    (total_chunks + 1) / (document_frequencies[token] + 1)
                ) + 1
                score += inverse_frequency * query_count * (1 + math.log(frequency))

            text_casefold = chunk.text.casefold()
            heading_casefold = chunk.section.casefold()
            title_casefold = chunk.document.title.casefold()
            if query_casefold in text_casefold:
                score += 8
            if query_casefold in heading_casefold:
                score += 7
            if query_casefold in title_casefold:
                score += 6
            if any(query_casefold in tag for tag in chunk.document.tags):
                score += 4
            if score > 0:
                scored.append((score, chunk))

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].document.source,
                item[1].start_line,
            )
        )
        results: list[dict[str, Any]] = []
        per_document: Counter[str] = Counter()
        for score, chunk in scored:
            if per_document[chunk.document.id] >= 2:
                continue
            per_document[chunk.document.id] += 1
            citation = (
                f"{chunk.document.title} > {chunk.section} "
                f"({chunk.document.source}:L{chunk.start_line}-L{chunk.end_line})"
            )
            results.append(
                {
                    "document_id": chunk.document.id,
                    "title": chunk.document.title,
                    "section": chunk.section,
                    "snippet": _compact_snippet(chunk.text),
                    "source": chunk.document.source,
                    "source_uri": f"kb://{chunk.document.id}",
                    "line_start": chunk.start_line,
                    "line_end": chunk.end_line,
                    "score": round(score, 4),
                    "tags": list(chunk.document.tags),
                    "updated_at": chunk.document.updated_at,
                    "citation": citation,
                }
            )
            if len(results) >= limit:
                break
        return {"query": normalized_query, "count": len(results), "results": results}

    def read_document(
        self,
        document_id: str,
        *,
        start_line: int = 1,
        end_line: int = 240,
    ) -> dict[str, Any]:
        document = next((item for item in self._documents() if item.id == document_id), None)
        if start_line < 1 or end_line < start_line:
            raise KnowledgeError("Use a valid positive line range")
        if end_line - start_line + 1 > MAX_READ_LINES:
            raise KnowledgeError(f"A single read is limited to {MAX_READ_LINES} lines")

        lines = document.content.splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered_content = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start_line)
        )
        actual_end = min(end_line, len(lines))
        return {
            "document_id": document.id,
            "title": document.title,
            "source": document.source,
            "source_uri": f"kb://{document.id}",
            "start_line": start_line,
            "end_line": actual_end,
            "total_lines": len(lines),
            "content": numbered_content,
            "citation": f"{document.title} ({document.source}:L{start_line}-L{actual_end})",
        }
