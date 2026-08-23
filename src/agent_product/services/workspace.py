from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid4, uuid5


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
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}

WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _is_blocked_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in BLOCKED_FILENAMES
        or (name.startswith(".env.") and name not in SAFE_ENV_TEMPLATES)
        or path.suffix.casefold() in BLOCKED_SUFFIXES
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_utf8(content: str, field_name: str) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspacePathError(f"{field_name} must be valid UTF-8 text") from exc


def _replace_file_windows(path: Path, replacement: Path) -> None:
    import ctypes
    from ctypes import wintypes

    original_security = _windows_security_sddl(path)
    backup = path.with_name(f".{path.name}.hajimi-{uuid4().hex}.backup")
    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    replace_file.restype = wintypes.BOOL
    if not replace_file(str(path), str(replacement), str(backup), 0x2, None, None):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(path))
    try:
        if _windows_security_sddl(path) != original_security:
            os.replace(backup, path)
            raise OSError("Windows security metadata changed during file replacement")
        backup.unlink()
    except OSError:
        if backup.exists():
            try:
                os.replace(backup, path)
            except OSError:
                pass
        raise


def _windows_security_sddl(path: Path) -> str:
    import ctypes
    from ctypes import wintypes

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security = security.GetNamedSecurityInfoW
    get_security.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    get_security.restype = wintypes.DWORD
    convert = security.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    descriptor = wintypes.LPVOID()
    result = get_security(
        str(path),
        1,
        0x7,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if result:
        raise OSError(result, ctypes.FormatError(result), str(path))
    try:
        string = wintypes.LPWSTR()
        if not convert(descriptor, 1, 0x7, ctypes.byref(string), None):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), str(path))
        try:
            return string.value
        finally:
            kernel32.LocalFree(string)
    finally:
        kernel32.LocalFree(descriptor)


def _move_file_windows(source: Path, destination: Path) -> None:
    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    move_file.restype = wintypes.BOOL
    if not move_file(str(source), str(destination)):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, ctypes.FormatError(error), str(destination))
        raise OSError(error, ctypes.FormatError(error), str(destination))


def _copy_posix_file_metadata(source: Path, destination: Path) -> None:
    original = source.stat(follow_symlinks=False)
    current = destination.stat(follow_symlinks=False)
    if hasattr(os, "chown") and (
        current.st_uid != original.st_uid or current.st_gid != original.st_gid
    ):
        os.chown(
            destination,
            original.st_uid,
            original.st_gid,
            follow_symlinks=False,
        )
    if all(hasattr(os, name) for name in ("listxattr", "getxattr", "setxattr")):
        for name in os.listxattr(source, follow_symlinks=False):
            if name == "security.capability":
                continue
            value = os.getxattr(source, name, follow_symlinks=False)
            try:
                os.setxattr(destination, name, value, follow_symlinks=False)
            except PermissionError:
                inherited = os.getxattr(destination, name, follow_symlinks=False)
                if inherited != value:
                    raise
    safe_mode = stat.S_IMODE(original.st_mode) & ~(stat.S_ISUID | stat.S_ISGID)
    os.chmod(destination, safe_mode, follow_symlinks=False)
    if hasattr(os, "chflags") and hasattr(original, "st_flags"):
        os.chflags(destination, original.st_flags, follow_symlinks=False)


def _replace_file_atomically(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.hajimi-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if os.name == "nt":
            _replace_file_windows(path, temporary)
        else:
            _copy_posix_file_metadata(path, temporary)
            os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise WorkspacePathError("The requested file could not be written") from exc


def _create_file_atomically(path: Path, content: bytes) -> None:
    """Publish a complete new file without replacing an existing directory entry."""
    temporary = path.with_name(f".{path.name}.hajimi-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if os.name == "nt":
            _move_file_windows(temporary, path)
        else:
            os.link(temporary, path)
    except FileExistsError as exc:
        raise WorkspacePathError("The requested file already exists") from exc
    except OSError as exc:
        raise WorkspacePathError("The requested file could not be created") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        path_stat = path.lstat()
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(path_stat, "st_file_attributes", 0)
    if not attributes & reparse_flag:
        return False
    reparse_tag = getattr(path_stat, "st_reparse_tag", None)
    if reparse_tag is None:
        return True
    return bool(reparse_tag & 0x20000000)


def _validate_path_component(part: str) -> None:
    if part in {".", ".."}:
        raise WorkspacePathError("The requested path is outside the workspace")
    if ":" in part:
        raise WorkspacePathError("Windows drive and alternate data stream paths are not allowed")
    if any(ord(character) < 32 for character in part):
        raise WorkspacePathError("Control characters are not allowed in workspace paths")
    if part.rstrip(" .") != part:
        raise WorkspacePathError("Path components may not end with a dot or space")
    device_name = part.split(".", maxsplit=1)[0].casefold()
    if device_name in WINDOWS_RESERVED_NAMES:
        raise WorkspacePathError("Windows reserved device names are not allowed")


@dataclass(frozen=True, slots=True)
class CodeWorkspace:
    id: str
    tenant_id: str
    root: Path
    _write_lock: RLock = field(default_factory=RLock, compare=False, repr=False)

    @property
    def name(self) -> str:
        return self.root.name

    def _resolve_path(self, relative_path: str) -> Path:
        if not relative_path:
            raise WorkspacePathError("Use a non-empty path relative to the workspace")

        try:
            requested = Path(relative_path)
        except (OSError, TypeError, ValueError) as exc:
            raise WorkspacePathError("The requested workspace path is invalid") from exc
        if requested.is_absolute() or requested.drive or requested.root:
            raise WorkspacePathError("Use a non-empty path relative to the workspace")

        for part in requested.parts:
            _validate_path_component(part)

        if any(part.casefold() in IGNORED_DIRECTORIES for part in requested.parts):
            raise WorkspacePathError("Access to ignored workspace directories is not allowed")

        try:
            candidate = (self.root / requested).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspacePathError("The requested workspace path could not be resolved") from exc
        if not candidate.is_relative_to(self.root):
            raise WorkspacePathError("The requested path is outside the workspace")
        resolved_relative = candidate.relative_to(self.root)
        if any(part.casefold() in IGNORED_DIRECTORIES for part in resolved_relative.parts):
            raise WorkspacePathError("Access to ignored workspace directories is not allowed")
        if _is_blocked_file(candidate):
            raise WorkspacePathError("Access to secret or credential files is not allowed")
        return candidate

    def resolve_file(self, relative_path: str) -> Path:
        candidate = self._resolve_path(relative_path)
        if not candidate.is_file():
            raise WorkspacePathError("The requested file does not exist")
        return candidate

    def _iter_files(self):
        for current_root, directories, filenames in os.walk(self.root, followlinks=False):
            current = Path(current_root)
            directories[:] = sorted(
                directory
                for directory in directories
                if directory.casefold() not in IGNORED_DIRECTORIES
                and not _is_link_or_reparse_point(current / directory)
            )
            for filename in sorted(filenames):
                path = current / filename
                if _is_link_or_reparse_point(path) or _is_blocked_file(path):
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if not resolved.is_file() or not resolved.is_relative_to(self.root):
                    continue
                resolved_relative = resolved.relative_to(self.root)
                if any(
                    part.casefold() in IGNORED_DIRECTORIES
                    for part in resolved_relative.parts
                ) or _is_blocked_file(resolved):
                    continue
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
        raw: bool = False,
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
            with path.open("rb") as file:
                raw_content = file.read(max_bytes + 1)
            if len(raw_content) > max_bytes:
                raise WorkspacePathError("The requested file is larger than 1 MB")
            text = raw_content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspacePathError("The requested file is not UTF-8 text") from exc
        except OSError as exc:
            raise WorkspacePathError("The requested file could not be read") from exc

        lines = text.splitlines()
        raw_lines = text.splitlines(keepends=True)
        selected = lines[start_line - 1 : end_line]
        selected_raw = raw_lines[start_line - 1 : end_line]
        complete = start_line == 1 and end_line >= len(lines)
        content = (
            "".join(selected_raw)
            if raw
            else "\n".join(
                f"{line_number}: {line}"
                for line_number, line in enumerate(selected, start=start_line)
            )
        )
        return {
            "path": path.relative_to(self.root).as_posix(),
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "total_lines": len(lines),
            "complete": complete,
            "sha256": sha256(raw_content).hexdigest() if complete else None,
            "content": content,
            "format": "raw" if raw else "numbered",
        }

    def write_file(
        self,
        relative_path: str,
        content: str,
        expected_sha256: str | None = None,
        max_bytes: int = 500_000,
    ) -> dict[str, object]:
        encoded = _encode_utf8(content, "content")
        if len(encoded) > max_bytes:
            raise WorkspacePathError("A single write is limited to 500 KB")
        with self._write_lock:
            return self._write_file_locked(
                relative_path,
                encoded,
                expected_sha256,
            )

    def _write_file_locked(
        self,
        relative_path: str,
        encoded: bytes,
        expected_sha256: str | None,
    ) -> dict[str, object]:
        path = self._resolve_path(relative_path)
        if path.exists() and not path.is_file():
            raise WorkspacePathError("The requested path is not a regular file")

        action = "created"
        previous_sha256: str | None = None
        if path.is_file():
            action = "updated"
            if expected_sha256 is None:
                raise WorkspacePathError(
                    "Read the existing file first and provide its sha256 before overwriting it"
                )
            try:
                if path.stat().st_size > 1_000_000:
                    raise WorkspacePathError(
                        "The existing file is larger than the 1 MB read limit"
                    )
                previous_sha256 = _sha256_file(path)
            except OSError as exc:
                raise WorkspacePathError("The requested file could not be read") from exc
            if expected_sha256.casefold() != previous_sha256:
                raise WorkspacePathError(
                    "The file changed after it was read; read it again before writing"
                )
        elif expected_sha256 is not None:
            raise WorkspacePathError(
                "The file no longer exists; omit expected_sha256 to create it"
            )

        if action == "created":
            created = self._create_file_locked(relative_path, encoded)
            return {**created, "previous_sha256": None}

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            resolved_after_create = path.resolve()
        except OSError as exc:
            raise WorkspacePathError("The destination directory could not be created") from exc
        if not resolved_after_create.is_relative_to(self.root):
            raise WorkspacePathError("The requested path is outside the workspace")

        try:
            if _sha256_file(path) != previous_sha256:
                raise WorkspacePathError(
                    "The file changed while the write was prepared; read it again before retrying"
                )
        except OSError as exc:
            raise WorkspacePathError(
                "The file changed while the write was prepared; read it again before retrying"
            ) from exc

        _replace_file_atomically(path, encoded)

        return {
            "path": path.relative_to(self.root).as_posix(),
            "action": action,
            "bytes_written": len(encoded),
            "previous_sha256": previous_sha256,
            "sha256": sha256(encoded).hexdigest(),
        }

    def create_file(
        self,
        relative_path: str,
        content: str,
        max_bytes: int = 500_000,
    ) -> dict[str, object]:
        """Create one UTF-8 file without ever replacing an existing directory entry."""
        encoded = _encode_utf8(content, "content")
        if len(encoded) > max_bytes:
            raise WorkspacePathError("A single create is limited to 500 KB")
        with self._write_lock:
            return self._create_file_locked(relative_path, encoded)

    def _create_file_locked(
        self,
        relative_path: str,
        encoded: bytes,
    ) -> dict[str, object]:
        path = self._resolve_path(relative_path)
        lexical_path = self.root / Path(relative_path)
        if lexical_path.exists() or lexical_path.is_symlink():
            raise WorkspacePathError("The requested file already exists")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path = self._resolve_path(relative_path)
        except OSError as exc:
            raise WorkspacePathError("The destination directory could not be created") from exc

        _create_file_atomically(path, encoded)

        return {
            "path": path.relative_to(self.root).as_posix(),
            "action": "created",
            "bytes_written": len(encoded),
            "sha256": sha256(encoded).hexdigest(),
        }

    def apply_patch(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        max_bytes: int = 500_000,
    ) -> dict[str, object]:
        """Replace one exact, unique UTF-8 text segment while preserving all other content."""
        if not old_text:
            raise WorkspacePathError("old_text must not be empty")
        if old_text == new_text:
            raise WorkspacePathError("The patch does not change the file")
        old_encoded = _encode_utf8(old_text, "old_text")
        new_encoded = _encode_utf8(new_text, "new_text")
        if len(old_encoded) > max_bytes or len(new_encoded) > max_bytes:
            raise WorkspacePathError("Patch text is limited to 500 KB")
        with self._write_lock:
            return self._apply_patch_locked(
                relative_path,
                old_text,
                new_text,
                expected_sha256,
                max_bytes,
            )

    def _apply_patch_locked(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None,
        max_bytes: int,
    ) -> dict[str, object]:
        path = self.resolve_file(relative_path)
        try:
            with path.open("rb") as file:
                raw_content = file.read(1_000_001)
            if len(raw_content) > 1_000_000:
                raise WorkspacePathError("The requested file is larger than 1 MB")
            content = raw_content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspacePathError("The requested file is not UTF-8 text") from exc
        except OSError as exc:
            raise WorkspacePathError("The requested file could not be read") from exc

        previous_sha256 = sha256(raw_content).hexdigest()
        if expected_sha256 is not None and expected_sha256.casefold() != previous_sha256:
            raise WorkspacePathError(
                "The file changed after it was read; read it again before applying a patch"
            )

        first_match = content.find(old_text)
        if first_match < 0:
            raise WorkspacePathError("old_text was not found exactly in the requested file")
        if content.find(old_text, first_match + 1) >= 0:
            raise WorkspacePathError(
                "old_text appears more than once; include more surrounding context"
            )

        updated = content.replace(old_text, new_text, 1)
        encoded = _encode_utf8(updated, "patched content")
        if len(encoded) > max_bytes:
            raise WorkspacePathError("The patched file would be larger than 500 KB")
        if _sha256_file(path) != previous_sha256:
            raise WorkspacePathError(
                "The file changed while the patch was prepared; read it again before retrying"
            )

        _replace_file_atomically(path, encoded)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "action": "patched",
            "bytes_written": len(encoded),
            "previous_sha256": previous_sha256,
            "sha256": sha256(encoded).hexdigest(),
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
        self._write_locks: dict[Path, RLock] = {}

    def create(self, path: str, tenant_id: str) -> CodeWorkspace:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise WorkspacePathError("The selected workspace directory does not exist")
        write_lock = self._write_locks.setdefault(root, RLock())
        workspace_id = str(
            uuid5(NAMESPACE_URL, f"{tenant_id}\0{os.path.normcase(str(root))}")
        )
        workspace = CodeWorkspace(
            id=workspace_id,
            tenant_id=tenant_id,
            root=root,
            _write_lock=write_lock,
        )
        self._workspaces[workspace.id] = workspace
        return workspace

    def get(self, workspace_id: str | None, tenant_id: str) -> CodeWorkspace | None:
        if workspace_id is None:
            return None
        workspace = self._workspaces.get(workspace_id)
        if workspace is None or workspace.tenant_id != tenant_id:
            raise WorkspaceNotFoundError("Workspace not found")
        return workspace
