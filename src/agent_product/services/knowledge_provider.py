from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_product.services.knowledge import KnowledgeError


@runtime_checkable
class KnowledgeProvider(Protocol):
    """Stable Agent-facing boundary over a knowledge source and its index."""

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
        library_ids: list[str] | None = None,
        include_context: bool = False,
    ) -> dict[str, Any]: ...

    def read_context(self, chunk_id: str) -> dict[str, Any]: ...

    def read_document(
        self,
        document_id: str,
        *,
        start_line: int = 1,
        end_line: int = 240,
    ) -> dict[str, Any]: ...

    def get_document(
        self,
        document_id: str,
        *,
        include_inactive: bool = False,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    """The durable knowledge visibility selected by an Agent Profile.

    The current filesystem provider exposes one implicit ``default`` library;
    V2 Library/Source providers can expose additional stable ids later.
    """

    scope_id: str
    required_tags: tuple[str, ...] = ()
    library_ids: tuple[str, ...] = ()


class KnowledgeScopeError(KnowledgeError):
    """Raised when a provider cannot enforce the profile's requested scope."""


class ScopedKnowledgeProvider:
    """Apply a Profile's read scope without changing the underlying index."""

    def __init__(self, provider: KnowledgeProvider, scope: KnowledgeScope) -> None:
        self.provider = provider
        self.scope = scope

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
        library_ids: list[str] | None = None,
        include_context: bool = False,
    ) -> dict[str, Any]:
        if library_ids is not None:
            raise KnowledgeScopeError("Callers cannot widen a Profile's knowledge libraries")
        requested = {tag.casefold().strip() for tag in tags or () if tag.strip()}
        requested.update(tag.casefold().strip() for tag in self.scope.required_tags)
        return self.provider.search(
            query,
            limit=limit,
            tags=sorted(requested) or None,
            library_ids=list(self.scope.library_ids) or None,
            include_context=include_context,
        )

    def read_context(self, chunk_id: str) -> dict[str, Any]:
        context = self.provider.read_context(chunk_id)
        if self.scope.library_ids and context.get("library_id") not in self.scope.library_ids:
            raise KnowledgeScopeError(
                f"Knowledge chunk {chunk_id!r} is outside this Agent's library scope"
            )
        if self.scope.required_tags:
            chunk_tags = {str(tag).casefold().strip() for tag in context.get("tags", ())}
            required = {tag.casefold().strip() for tag in self.scope.required_tags}
            if not required.issubset(chunk_tags):
                raise KnowledgeScopeError(
                    f"Knowledge chunk {chunk_id!r} is outside this Agent's scope"
                )
        return context

    def read_document(
        self,
        document_id: str,
        *,
        start_line: int = 1,
        end_line: int = 240,
    ) -> dict[str, Any]:
        metadata = self.provider.get_document(document_id)
        if self.scope.library_ids and metadata.get("library_id") not in self.scope.library_ids:
            raise KnowledgeScopeError(
                f"Knowledge document {document_id!r} is outside this Agent's library scope"
            )
        if self.scope.required_tags:
            document_tags = {
                str(tag).casefold().strip() for tag in metadata.get("tags", ())
            }
            required = {tag.casefold().strip() for tag in self.scope.required_tags}
            if not required.issubset(document_tags):
                raise KnowledgeScopeError(
                    f"Knowledge document {document_id!r} is outside this Agent's scope"
                )
        return self.provider.read_document(
            document_id,
            start_line=start_line,
            end_line=end_line,
        )

    def get_document(
        self,
        document_id: str,
        *,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        if include_inactive:
            raise KnowledgeScopeError("Agent knowledge access cannot include inactive documents")
        self.read_document(document_id, start_line=1, end_line=1)
        return self.provider.get_document(document_id)
