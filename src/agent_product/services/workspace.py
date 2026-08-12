from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class WorkspaceError(ValueError):
    """Base error for invalid or unsafe workspace operations."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a workspace does not exist or belongs to another tenant."""


class WorkspacePathError(WorkspaceError):
    """Raised when a requested path escapes the workspace or is not readable."""


IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "vendor",
}

BLOCKED_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}

BLOCKED_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}


def _is_blocked_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in BLOCKED_FILENAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in BLOCKED_SUFFIXES
    )


@dataclass(frozen=True, slots=True)
class CodeWorkspace:
    id: str
    tenant_id: str
    root: Path

    @property
    def name(self) -> str:
        return self.root.name

    def resolve_file(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise WorkspacePathError("Use a non-empty path relative to the workspace")

        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspacePathError("The requested path is outside the workspace")
        if _is_blocked_file(candidate):
            raise WorkspacePathError("Reading secret or credential files is not allowed")
        if not candidate.is_file():
            raise WorkspacePathError("The requested file does not exist")
        return candidate

    def _iter_files(self):
        for current_root, directories, filenames in os.walk(self.root, followlinks=False):
            current = Path(current_root)
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in IGNORED_DIRECTORIES
                and not (current / directory).is_symlink()
            )
            for filename in sorted(filenames):
                path = current / filename
                if _is_blocked_file(path):
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved.is_file() and resolved.is_relative_to(self.root):
                    yield resolved

    def list_files(self, pattern: str | None = None, limit: int = 200) -> dict[str, object]:
        normalized_pattern = pattern.casefold().strip() if pattern else None
        files: list[str] = []
        truncated = False
        for path in self._iter_files():
            relative = path.relative_to(self.root).as_posix()
            if normalized_pattern and normalized_pattern not in relative.casefold():
                continue
            if len(files) >= limit:
                truncated = True
                break
            files.append(relative)
        return {"workspace": self.name, "files": files, "truncated": truncated}

    def read_file(
        self,
        relative_path: str,
        start_line: int = 1,
        end_line: int = 240,
        max_bytes: int = 1_000_000,
    ) -> dict[str, object]:
        if start_line < 1 or end_line < start_line:
            raise WorkspacePathError("Line range is invalid")
        if end_line - start_line + 1 > 400:
            raise WorkspacePathError("A single read is limited to 400 lines")

        path = self.resolve_file(relative_path)
        if path.stat().st_size > max_bytes:
            raise WorkspacePathError("The requested file is larger than 1 MB")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspacePathError("The requested file is not UTF-8 text") from exc
        except OSError as exc:
            raise WorkspacePathError("The requested file could not be read") from exc

        lines = text.splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start_line)
        )
        return {
            "path": path.relative_to(self.root).as_posix(),
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "total_lines": len(lines),
            "content": numbered,
        }

    def search_text(
        self,
        query: str,
        path_filter: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        normalized_query = query.casefold().strip()
        if not normalized_query or len(normalized_query) > 200:
            raise WorkspacePathError("Search query must contain between 1 and 200 characters")
        normalized_filter = path_filter.casefold().strip() if path_filter else None

        matches: list[dict[str, object]] = []
        skipped_files = 0
        for path in self._iter_files():
            relative = path.relative_to(self.root).as_posix()
            if normalized_filter and normalized_filter not in relative.casefold():
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    skipped_files += 1
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                skipped_files += 1
                continue
            for line_number, line in enumerate(lines, start=1):
                if normalized_query not in line.casefold():
                    continue
                matches.append(
                    {
                        "path": relative,
                        "line_number": line_number,
                        "line": line[:500],
                    }
                )
                if len(matches) >= limit:
                    return {
                        "query": query,
                        "matches": matches,
                        "truncated": True,
                        "skipped_files": skipped_files,
                    }
        return {
            "query": query,
            "matches": matches,
            "truncated": False,
            "skipped_files": skipped_files,
        }


class WorkspaceRegistry:
    """Process-local registry of user-approved code workspaces."""

    def __init__(self) -> None:
        self._workspaces: dict[str, CodeWorkspace] = {}

    def create(self, path: str, tenant_id: str) -> CodeWorkspace:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise WorkspacePathError("The selected workspace directory does not exist")
        workspace = CodeWorkspace(id=str(uuid4()), tenant_id=tenant_id, root=root)
        self._workspaces[workspace.id] = workspace
        return workspace

    def get(self, workspace_id: str | None, tenant_id: str) -> CodeWorkspace | None:
        if workspace_id is None:
            return None
        workspace = self._workspaces.get(workspace_id)
        if workspace is None or workspace.tenant_id != tenant_id:
            raise WorkspaceNotFoundError("Workspace not found")
        return workspace
