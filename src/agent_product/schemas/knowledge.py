from pydantic import BaseModel, Field


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=5, ge=1, le=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    library_ids: list[str] = Field(default_factory=list, max_length=20)
    include_context: bool = False


class KnowledgeSearchHit(BaseModel):
    library_id: str
    source_id: str
    document_id: str
    title: str
    section: str
    heading_path: list[str]
    chunk_id: str
    parent_chunk_id: str
    chunk_policy_version: int
    token_count: int
    snippet: str
    context: str | None = None
    context_line_start: int | None = None
    context_line_end: int | None = None
    source: str
    source_uri: str
    line_start: int
    line_end: int
    score: float
    lexical_score: float
    vector_score: float | None = None
    rerank_score: float | None = None
    tags: list[str]
    updated_at: str
    citation: str
    context_citation: str | None = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    count: int
    chunk_policy_version: int
    results: list[KnowledgeSearchHit]


class KnowledgeContextResponse(BaseModel):
    library_id: str
    source_id: str
    document_id: str
    title: str
    section: str
    heading_path: list[str]
    chunk_id: str
    parent_chunk_id: str
    context: str
    context_line_start: int
    context_line_end: int
    source: str
    source_uri: str
    tags: list[str]
    updated_at: str
    revision: str
    citation: str


class KnowledgeChunkPolicyResponse(BaseModel):
    version: int
    strategy: str
    target_tokens: int
    soft_max_tokens: int
    hard_max_tokens: int
    min_tokens: int
    forced_split_overlap_tokens: int
    natural_boundary_overlap_tokens: int
    parent_context_target_tokens: int
    parent_context_max_tokens: int


class KnowledgeIndexSyncResponse(BaseModel):
    added: int
    updated: int
    removed: int
    total: int


class KnowledgeIndexStatusResponse(BaseModel):
    backend: str
    retrieval_modes: list[str]
    chunk_count: int
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    last_sync: KnowledgeIndexSyncResponse


class KnowledgeLibrarySummary(BaseModel):
    library_id: str
    name: str
    status: str
    document_count: int
    retrievable_document_count: int
    source_count: int


class KnowledgeSourceSummary(BaseModel):
    library_id: str
    source_id: str
    kind: str
    name: str
    status: str
    document_ids: list[str]
    source: str
    revision: str
    updated_at: str


class KnowledgeDocumentSummary(BaseModel):
    library_id: str
    source_id: str
    document_id: str
    title: str
    summary: str
    tags: list[str]
    status: str
    source: str
    updated_at: str
    revision: str


class KnowledgeDocumentResponse(BaseModel):
    library_id: str
    source_id: str
    document_id: str
    title: str
    source: str
    source_uri: str
    start_line: int
    end_line: int
    total_lines: int
    content: str
    citation: str


class KnowledgeManagedDocument(KnowledgeDocumentSummary):
    body: str
    source_uri: str | None = None
    action: str | None = None


class KnowledgeDocumentWriteRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=1_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    status: str = Field(default="draft", max_length=20)
    body: str = Field(default="", max_length=1_000_000)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)
