from pydantic import BaseModel, Field


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=5, ge=1, le=20)
    tags: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeSearchHit(BaseModel):
    document_id: str
    title: str
    section: str
    snippet: str
    source: str
    source_uri: str
    line_start: int
    line_end: int
    score: float
    tags: list[str]
    updated_at: str
    citation: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    count: int
    results: list[KnowledgeSearchHit]


class KnowledgeDocumentSummary(BaseModel):
    document_id: str
    title: str
    summary: str
    tags: list[str]
    status: str
    source: str
    updated_at: str
    revision: str


class KnowledgeDocumentResponse(BaseModel):
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
