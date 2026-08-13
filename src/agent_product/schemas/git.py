from typing import Literal

from pydantic import BaseModel, Field


class GitFileChange(BaseModel):
    path: str
    previous_path: str | None = None
    status: str
    staged: bool
    unstaged: bool
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    diff: str = ""
    diff_truncated: bool = False


class ReviewCheck(BaseModel):
    id: str
    name: str
    kind: Literal["integrity", "test", "lint", "build"]
    status: Literal["passed", "failed", "not_run"]
    summary: str
    output: str = ""


class GitReviewResponse(BaseModel):
    repository: str
    branch: str | None
    head: str | None
    upstream: str | None
    ahead: int = 0
    behind: int = 0
    clean: bool
    files: list[GitFileChange]
    additions: int = 0
    deletions: int = 0
    restricted_changes: int = 0
    diff_truncated: bool = False
    checks: list[ReviewCheck]


class GitCommitPrepareRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class GitConfirmationResponse(BaseModel):
    confirmation_id: str
    action: Literal["commit", "push"]
    title: str
    details: list[str]
    expires_at: str


class GitConfirmRequest(BaseModel):
    confirmation_id: str = Field(min_length=1, max_length=100)


class GitCommitResponse(BaseModel):
    commit: str
    subject: str
    files_committed: int


class GitPushResponse(BaseModel):
    remote: str
    branch: str
    head: str
    summary: str
