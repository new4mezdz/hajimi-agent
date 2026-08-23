from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol, runtime_checkable

_LEXICAL_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*|[\u3400-\u9fff]+")


@dataclass(frozen=True, slots=True)
class KnowledgeIndexChunk:
    chunk_id: str
    parent_chunk_id: str
    document_id: str
    title: str
    summary: str
    source: str
    source_uri: str
    heading_path: tuple[str, ...]
    tags: tuple[str, ...]
    content: str
    context: str
    line_start: int
    line_end: int
    context_line_start: int
    context_line_end: int
    token_count: int
    policy_version: int
    updated_at: str
    revision: str
    content_hash: str
    library_id: str = "default"
    source_id: str = ""

    @property
    def section(self) -> str:
        return self.heading_path[-1] if self.heading_path else self.title

    @property
    def embedding_text(self) -> str:
        """Canonical text passed to lexical and future embedding backends."""
        parts = [
            self.title,
            " > ".join(self.heading_path),
            self.summary,
            f"tags: {', '.join(self.tags)}" if self.tags else "",
            self.content,
        ]
        return "\n".join(part for part in parts if part)


@dataclass(frozen=True, slots=True)
class KnowledgeIndexQuery:
    text: str
    limit: int = 5
    required_tags: frozenset[str] = frozenset()
    library_ids: frozenset[str] = frozenset()
    per_document_limit: int = 2


@dataclass(frozen=True, slots=True)
class KnowledgeIndexMatch:
    chunk: KnowledgeIndexChunk
    score: float
    lexical_score: float = 0.0
    vector_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeIndexSyncResult:
    added: int
    updated: int
    removed: int
    total: int


@dataclass(frozen=True, slots=True)
class KnowledgeIndexStatus:
    backend: str
    retrieval_modes: tuple[str, ...]
    chunk_count: int
    embedding_model: str | None = None
    embedding_dimensions: int | None = None


@runtime_checkable
class EmbeddingModel(Protocol):
    """Adapter boundary for local or remote embedding implementations."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


@runtime_checkable
class KnowledgeIndex(Protocol):
    """A replaceable lexical, vector, or hybrid chunk index."""

    def sync(self, chunks: Sequence[KnowledgeIndexChunk]) -> KnowledgeIndexSyncResult: ...

    def search(self, query: KnowledgeIndexQuery) -> list[KnowledgeIndexMatch]: ...

    def get(self, chunk_id: str) -> KnowledgeIndexChunk | None: ...

    def status(self) -> KnowledgeIndexStatus: ...


def _lexical_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _LEXICAL_TOKEN_PATTERN.finditer(text.casefold()):
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


class InMemoryLexicalKnowledgeIndex:
    """The dependency-free lexical backend used before a persistent hybrid index."""

    def __init__(self) -> None:
        self._chunks: dict[str, KnowledgeIndexChunk] = {}

    def sync(self, chunks: Sequence[KnowledgeIndexChunk]) -> KnowledgeIndexSyncResult:
        incoming = {chunk.chunk_id: chunk for chunk in chunks}
        added = sum(chunk_id not in self._chunks for chunk_id in incoming)
        updated = sum(
            chunk_id in self._chunks and self._chunks[chunk_id].content_hash != chunk.content_hash
            for chunk_id, chunk in incoming.items()
        )
        removed = sum(chunk_id not in incoming for chunk_id in self._chunks)
        self._chunks = incoming
        return KnowledgeIndexSyncResult(
            added=added,
            updated=updated,
            removed=removed,
            total=len(incoming),
        )

    def search(self, query: KnowledgeIndexQuery) -> list[KnowledgeIndexMatch]:
        chunks = [
            chunk
            for chunk in self._chunks.values()
            if (not query.required_tags or query.required_tags.issubset(chunk.tags))
            and (not query.library_ids or chunk.library_id in query.library_ids)
        ]
        query_tokens = Counter(_lexical_tokens(query.text))
        if not chunks or not query_tokens:
            return []

        chunk_tokens: list[Counter[str]] = []
        document_frequencies: Counter[str] = Counter()
        for chunk in chunks:
            counts = Counter(_lexical_tokens(chunk.embedding_text))
            chunk_tokens.append(counts)
            document_frequencies.update(set(counts) & set(query_tokens))

        scored: list[KnowledgeIndexMatch] = []
        total_chunks = len(chunks)
        normalized_query = query.text.casefold()
        for chunk, counts in zip(chunks, chunk_tokens, strict=True):
            score = 0.0
            for token, query_count in query_tokens.items():
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                inverse_frequency = (
                    math.log((total_chunks + 1) / (document_frequencies[token] + 1)) + 1
                )
                score += inverse_frequency * query_count * (1 + math.log(frequency))

            if normalized_query in chunk.content.casefold():
                score += 8
            if normalized_query in chunk.section.casefold():
                score += 7
            if normalized_query in chunk.title.casefold():
                score += 6
            if any(normalized_query in tag for tag in chunk.tags):
                score += 4
            if score > 0:
                scored.append(
                    KnowledgeIndexMatch(
                        chunk=chunk,
                        score=round(score, 4),
                        lexical_score=round(score, 4),
                    )
                )

        scored.sort(
            key=lambda match: (
                -match.score,
                match.chunk.source,
                match.chunk.line_start,
            )
        )
        results: list[KnowledgeIndexMatch] = []
        per_document: Counter[str] = Counter()
        for match in scored:
            document_id = match.chunk.document_id
            if per_document[document_id] >= max(1, query.per_document_limit):
                continue
            per_document[document_id] += 1
            results.append(match)
            if len(results) >= max(1, query.limit):
                break
        return results

    def get(self, chunk_id: str) -> KnowledgeIndexChunk | None:
        return self._chunks.get(chunk_id)

    def status(self) -> KnowledgeIndexStatus:
        return KnowledgeIndexStatus(
            backend="memory_lexical",
            retrieval_modes=("lexical",),
            chunk_count=len(self._chunks),
        )


class SQLiteFtsKnowledgeIndex:
    """Persistent, incrementally synchronized FTS5 implementation of KnowledgeIndex."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_index_meta "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            schema_row = self._connection.execute(
                "SELECT value FROM knowledge_index_meta WHERE key = 'schema_version'"
            ).fetchone()
            if schema_row is not None and str(schema_row[0]) != "1":
                raise RuntimeError(
                    f"Unsupported knowledge index schema version: {schema_row[0]}"
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO knowledge_index_meta(key, value) "
                "VALUES ('schema_version', '1')"
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
                USING fts5(chunk_id UNINDEXED, search_text, tokenize='unicode61')
                """
            )
            self._connection.commit()
        except (sqlite3.Error, RuntimeError):
            self._connection.close()
            raise

    @staticmethod
    def _encode_chunk(chunk: KnowledgeIndexChunk) -> str:
        return json.dumps(
            asdict(chunk),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_chunk(payload: str) -> KnowledgeIndexChunk:
        data = json.loads(payload)
        data["heading_path"] = tuple(data["heading_path"])
        data["tags"] = tuple(data["tags"])
        return KnowledgeIndexChunk(**data)

    @staticmethod
    def _search_text(chunk: KnowledgeIndexChunk) -> str:
        # FTS5's unicode tokenizer does not segment Chinese. Persisting the same
        # unigrams/bigrams/trigrams as the in-memory backend keeps Chinese queries useful.
        return " ".join(_lexical_tokens(chunk.embedding_text))

    def sync(self, chunks: Sequence[KnowledgeIndexChunk]) -> KnowledgeIndexSyncResult:
        incoming = {chunk.chunk_id: chunk for chunk in chunks}
        with self._lock, self._connection:
            existing = {
                str(row["chunk_id"]): str(row["content_hash"])
                for row in self._connection.execute(
                    "SELECT chunk_id, content_hash FROM knowledge_chunks"
                )
            }
            removed_ids = sorted(set(existing) - set(incoming))
            added_ids = sorted(set(incoming) - set(existing))
            updated_ids = sorted(
                chunk_id
                for chunk_id in set(existing) & set(incoming)
                if existing[chunk_id] != incoming[chunk_id].content_hash
            )
            for chunk_id in removed_ids + updated_ids:
                self._connection.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                    (chunk_id,),
                )
                self._connection.execute(
                    "DELETE FROM knowledge_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                )
            for chunk_id in added_ids + updated_ids:
                chunk = incoming[chunk_id]
                self._connection.execute(
                    "INSERT INTO knowledge_chunks(chunk_id, content_hash, payload_json) "
                    "VALUES (?, ?, ?)",
                    (chunk.chunk_id, chunk.content_hash, self._encode_chunk(chunk)),
                )
                self._connection.execute(
                    "INSERT INTO knowledge_chunks_fts(chunk_id, search_text) VALUES (?, ?)",
                    (chunk.chunk_id, self._search_text(chunk)),
                )
            total = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks"
                ).fetchone()[0]
            )
        return KnowledgeIndexSyncResult(
            added=len(added_ids),
            updated=len(updated_ids),
            removed=len(removed_ids),
            total=total,
        )

    def search(self, query: KnowledgeIndexQuery) -> list[KnowledgeIndexMatch]:
        tokens = sorted(set(_lexical_tokens(query.text)))[:64]
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        with self._lock:
            rows = list(
                self._connection.execute(
                    """
                    SELECT c.payload_json, bm25(knowledge_chunks_fts) AS rank
                    FROM knowledge_chunks_fts
                    JOIN knowledge_chunks AS c USING (chunk_id)
                    WHERE knowledge_chunks_fts MATCH ?
                    ORDER BY rank, c.chunk_id
                    LIMIT 1000
                    """,
                    (fts_query,),
                )
            )
        normalized_query = query.text.casefold()
        query_counts = Counter(_lexical_tokens(query.text))
        decoded: list[tuple[KnowledgeIndexChunk, float, Counter[str]]] = []
        document_frequencies: Counter[str] = Counter()
        for row in rows:
            chunk = self._decode_chunk(str(row["payload_json"]))
            if query.required_tags and not query.required_tags.issubset(chunk.tags):
                continue
            if query.library_ids and chunk.library_id not in query.library_ids:
                continue
            counts = Counter(_lexical_tokens(chunk.embedding_text))
            decoded.append((chunk, float(row["rank"]), counts))
            document_frequencies.update(set(counts) & set(query_counts))
        candidates: list[KnowledgeIndexMatch] = []
        total_chunks = len(decoded)
        for chunk, rank, counts in decoded:
            score = 0.0
            for token, query_count in query_counts.items():
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                inverse_frequency = (
                    math.log((total_chunks + 1) / (document_frequencies[token] + 1)) + 1
                )
                score += inverse_frequency * query_count * (1 + math.log(frequency))
            score += 1.0 / (1.0 + abs(rank))
            if normalized_query in chunk.content.casefold():
                score += 8
            if normalized_query in chunk.section.casefold():
                score += 7
            if normalized_query in chunk.title.casefold():
                score += 6
            if any(normalized_query in tag for tag in chunk.tags):
                score += 4
            lexical_score = round(score, 4)
            candidates.append(
                KnowledgeIndexMatch(
                    chunk=chunk,
                    score=lexical_score,
                    lexical_score=lexical_score,
                )
            )
        candidates.sort(
            key=lambda match: (-match.score, match.chunk.source, match.chunk.line_start)
        )
        results: list[KnowledgeIndexMatch] = []
        per_document: Counter[str] = Counter()
        for match in candidates:
            if per_document[match.chunk.document_id] >= max(1, query.per_document_limit):
                continue
            per_document[match.chunk.document_id] += 1
            results.append(match)
            if len(results) >= max(1, query.limit):
                break
        return results

    def get(self, chunk_id: str) -> KnowledgeIndexChunk | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM knowledge_chunks WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_chunk(str(row["payload_json"]))

    def status(self) -> KnowledgeIndexStatus:
        with self._lock:
            count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks"
                ).fetchone()[0]
            )
        return KnowledgeIndexStatus(
            backend="sqlite_fts5",
            retrieval_modes=("lexical", "persistent"),
            chunk_count=count,
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def create_knowledge_index(
    backend: str,
    *,
    sqlite_path: str | Path = "data/knowledge-index.db",
) -> KnowledgeIndex:
    if backend == "memory":
        return InMemoryLexicalKnowledgeIndex()
    if backend == "sqlite_fts5":
        return SQLiteFtsKnowledgeIndex(sqlite_path)
    raise ValueError("Knowledge index backend must be 'memory' or 'sqlite_fts5'")
