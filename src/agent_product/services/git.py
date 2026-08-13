from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_product.services.workspace import CodeWorkspace, WorkspacePathError


class GitError(ValueError):
    """Raised when a Git operation is invalid, unsafe, or fails."""


class GitConfirmationError(GitError):
    """Raised when an operation is not backed by a valid confirmation."""


@dataclass(frozen=True, slots=True)
class GitChange:
    path: str
    previous_path: str | None
    index_status: str
    worktree_status: str

    @property
    def staged(self) -> bool:
        return self.index_status not in {".", "?"}

    @property
    def unstaged(self) -> bool:
        return self.worktree_status != "." or self.index_status == "?"

    @property
    def status(self) -> str:
        codes = (self.index_status, self.worktree_status)
        if "U" in codes:
            return "conflicted"
        for code, label in (
            ("R", "renamed"),
            ("C", "copied"),
            ("A", "added"),
            ("D", "deleted"),
            ("M", "modified"),
            ("T", "type-changed"),
            ("?", "untracked"),
        ):
            if code in codes:
                return label
        return "changed"


@dataclass(frozen=True, slots=True)
class GitIntent:
    id: str
    action: str
    tenant_id: str
    workspace_id: str
    fingerprint: str
    expires_at: datetime
    message: str | None = None
    remote: str | None = None
    branch: str | None = None
    set_upstream: bool = False


class GitIntentRegistry:
    """One-time, process-local confirmations bound to exact repository state."""

    def __init__(self, lifetime_seconds: int = 120) -> None:
        self._lifetime = timedelta(seconds=lifetime_seconds)
        self._intents: dict[str, GitIntent] = {}
        self._lock = threading.Lock()

    def create(self, **values) -> GitIntent:
        now = datetime.now(UTC)
        intent = GitIntent(
            id=str(uuid4()),
            expires_at=now + self._lifetime,
            **values,
        )
        with self._lock:
            self._remove_expired(now)
            self._intents[intent.id] = intent
        return intent

    def consume(
        self,
        confirmation_id: str,
        *,
        action: str,
        tenant_id: str,
        workspace_id: str,
    ) -> GitIntent:
        now = datetime.now(UTC)
        with self._lock:
            self._remove_expired(now)
            intent = self._intents.pop(confirmation_id, None)
        if intent is None:
            raise GitConfirmationError("Confirmation is missing, expired, or already used")
        if (
            intent.action != action
            or intent.tenant_id != tenant_id
            or intent.workspace_id != workspace_id
        ):
            raise GitConfirmationError("Confirmation does not match this Git operation")
        return intent

    def _remove_expired(self, now: datetime) -> None:
        expired = [key for key, value in self._intents.items() if value.expires_at <= now]
        for key in expired:
            self._intents.pop(key, None)


class GitService:
    MAX_COMMAND_OUTPUT = 4_000_000
    MAX_DIFF_TOTAL = 800_000
    MAX_DIFF_PER_FILE = 240_000

    def __init__(self, workspace: CodeWorkspace) -> None:
        self.workspace = workspace
        self.root = workspace.root
        disabled_hooks = Path(tempfile.gettempdir()) / "hajimi-agent-disabled-git-hooks-v1"
        disabled_hooks.mkdir(exist_ok=True)
        self._git_prefix = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={disabled_hooks}",
            "-c",
            "core.quotepath=false",
        ]
        self._ensure_repository_root()

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "LC_ALL": "C",
            }
        )
        return environment

    def _run(
        self,
        *arguments: str,
        timeout: int = 15,
        allow_failure: bool = False,
        max_output: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                [*self._git_prefix, *arguments],
                cwd=self.root,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("Git is not installed or is not available in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("Git operation timed out") from exc
        limit = max_output or self.MAX_COMMAND_OUTPUT
        if len(result.stdout) + len(result.stderr) > limit:
            raise GitError("Git output exceeded the safety limit")
        if result.returncode and not allow_failure:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitError(detail or "Git operation failed")
        return result

    @staticmethod
    def _text(result: subprocess.CompletedProcess[bytes]) -> str:
        return result.stdout.decode("utf-8", errors="replace").strip()

    def _ensure_repository_root(self) -> None:
        result = self._run("rev-parse", "--show-toplevel")
        repository_root = Path(self._text(result)).resolve()
        if repository_root != self.root:
            raise GitError("Select the Git repository root, not a parent or nested directory")

    def _safe_path(self, path: str) -> bool:
        try:
            self.workspace._resolve_path(path)
        except WorkspacePathError:
            return False
        return True

    def status(self) -> tuple[dict[str, object], list[GitChange], int]:
        raw = self._run(
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            "--untracked-files=all",
        ).stdout
        records = raw.decode("utf-8", errors="surrogateescape").split("\0")
        branch: dict[str, object] = {
            "branch": None,
            "head": None,
            "upstream": None,
            "ahead": 0,
            "behind": 0,
        }
        changes: list[GitChange] = []
        restricted = 0
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if record.startswith("# branch.oid "):
                oid = record.removeprefix("# branch.oid ")
                branch["head"] = None if oid == "(initial)" else oid
                continue
            if record.startswith("# branch.head "):
                name = record.removeprefix("# branch.head ")
                branch["branch"] = None if name == "(detached)" else name
                continue
            if record.startswith("# branch.upstream "):
                branch["upstream"] = record.removeprefix("# branch.upstream ")
                continue
            if record.startswith("# branch.ab "):
                ahead, behind = record.removeprefix("# branch.ab ").split(" ", 1)
                branch["ahead"] = int(ahead.removeprefix("+"))
                branch["behind"] = abs(int(behind))
                continue

            previous_path: str | None = None
            if record.startswith("1 "):
                parts = record.split(" ", 8)
                xy, path = parts[1], parts[8]
            elif record.startswith("2 "):
                parts = record.split(" ", 9)
                xy, path = parts[1], parts[9]
                if index < len(records):
                    previous_path = records[index]
                    index += 1
            elif record.startswith("u "):
                parts = record.split(" ", 10)
                xy, path = parts[1], parts[10]
            elif record.startswith("? "):
                xy, path = "??", record[2:]
            else:
                continue

            if not self._safe_path(path) or (
                previous_path is not None and not self._safe_path(previous_path)
            ):
                restricted += 1
                continue
            changes.append(
                GitChange(
                    path=path,
                    previous_path=previous_path,
                    index_status=xy[0],
                    worktree_status=xy[1],
                )
            )
        return branch, changes, restricted

    def _diff_for_change(self, change: GitChange) -> tuple[str, bool, int, int, bool]:
        sections: list[str] = []
        binary = False
        if change.staged:
            staged = self._text(
                self._run(
                    "diff",
                    "--cached",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-color",
                    "--unified=3",
                    "--",
                    change.path,
                )
            )
            if staged:
                sections.append(staged)
        if change.unstaged and change.index_status != "?":
            unstaged = self._text(
                self._run(
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-color",
                    "--unified=3",
                    "--",
                    change.path,
                )
            )
            if unstaged:
                sections.append(unstaged)
        if change.index_status == "?":
            sections.append(self._untracked_diff(change.path))

        diff = "\n\n".join(section for section in sections if section)
        binary = "Binary files " in diff or "GIT binary patch" in diff
        additions, deletions = self._count_changes(diff)
        encoded = diff.encode("utf-8", errors="replace")
        truncated = len(encoded) > self.MAX_DIFF_PER_FILE
        if truncated:
            diff = encoded[: self.MAX_DIFF_PER_FILE].decode("utf-8", errors="ignore")
            diff += "\n\n... Diff truncated by Hajimi Agent ..."
        return diff, binary, additions, deletions, truncated

    def _untracked_diff(self, path: str) -> str:
        file_path = self.workspace._resolve_path(path)
        try:
            content = file_path.read_bytes()
        except OSError as exc:
            raise GitError(f"Could not read untracked file: {path}") from exc
        if len(content) > self.MAX_DIFF_PER_FILE:
            return f"diff --git a/{path} b/{path}\nnew file (content exceeds preview limit)"
        if b"\0" in content:
            return f"diff --git a/{path} b/{path}\nBinary files /dev/null and b/{path} differ"
        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        header = [
            f"diff --git a/{path} b/{path}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{path}",
        ]
        if not lines:
            return "\n".join(header)
        body = [f"@@ -0,0 +1,{len(lines)} @@", *(f"+{line}" for line in lines)]
        return "\n".join([*header, *body])

    @staticmethod
    def _count_changes(diff: str) -> tuple[int, int]:
        additions = sum(
            1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
        )
        return additions, deletions

    def review(self) -> dict[str, object]:
        branch, changes, restricted = self.status()
        files: list[dict[str, object]] = []
        additions = 0
        deletions = 0
        total_bytes = 0
        truncated = False
        for change in changes:
            diff, binary, added, deleted, file_truncated = self._diff_for_change(change)
            total_bytes += len(diff.encode("utf-8", errors="replace"))
            if total_bytes > self.MAX_DIFF_TOTAL:
                diff = "Diff preview omitted because the review reached its total size limit."
                file_truncated = True
                truncated = True
            additions += added
            deletions += deleted
            files.append(
                {
                    "path": change.path,
                    "previous_path": change.previous_path,
                    "status": change.status,
                    "staged": change.staged,
                    "unstaged": change.unstaged,
                    "additions": added,
                    "deletions": deleted,
                    "binary": binary,
                    "diff": diff,
                    "diff_truncated": file_truncated,
                }
            )

        integrity = self._run(
            "diff",
            "--check",
            "--no-ext-diff",
            "--no-textconv",
            allow_failure=True,
        )
        integrity_cached = self._run(
            "diff",
            "--cached",
            "--check",
            "--no-ext-diff",
            "--no-textconv",
            allow_failure=True,
        )
        integrity_output = "\n".join(
            part
            for part in (
                integrity.stdout.decode("utf-8", errors="replace").strip(),
                integrity_cached.stdout.decode("utf-8", errors="replace").strip(),
            )
            if part
        )
        integrity_passed = integrity.returncode == 0 and integrity_cached.returncode == 0
        checks = [
            {
                "id": "git-diff-check",
                "name": "Git diff integrity",
                "kind": "integrity",
                "status": "passed" if integrity_passed else "failed",
                "summary": (
                    "No whitespace errors found"
                    if integrity_passed
                    else "Whitespace errors require attention"
                ),
                "output": integrity_output,
            },
            {
                "id": "project-tests",
                "name": "Project tests",
                "kind": "test",
                "status": "not_run",
                "summary": "No sandboxed test run has been recorded for this review",
                "output": "",
            },
        ]
        return {
            "repository": self.workspace.name,
            **branch,
            "clean": not changes and restricted == 0,
            "files": files,
            "additions": additions,
            "deletions": deletions,
            "restricted_changes": restricted,
            "diff_truncated": truncated or any(file["diff_truncated"] for file in files),
            "checks": checks,
        }

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._run("rev-parse", "HEAD", allow_failure=True).stdout)
        digest.update(
            self._run(
                "status", "--porcelain=v2", "-z", "--untracked-files=all"
            ).stdout
        )
        digest.update(
            self._run(
                "diff", "--binary", "--no-ext-diff", "--no-textconv", max_output=16_000_000
            ).stdout
        )
        digest.update(
            self._run(
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                max_output=16_000_000,
            ).stdout
        )
        _, changes, restricted = self.status()
        digest.update(str(restricted).encode())
        for change in changes:
            if change.index_status != "?":
                continue
            path = self.workspace._resolve_path(change.path)
            digest.update(change.path.encode("utf-8"))
            try:
                with path.open("rb") as file:
                    for chunk in iter(lambda: file.read(64 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise GitError(f"Could not fingerprint untracked file: {change.path}") from exc
        return digest.hexdigest()

    def prepare_commit(
        self,
        registry: GitIntentRegistry,
        *,
        tenant_id: str,
        message: str,
    ) -> tuple[GitIntent, list[str]]:
        normalized_message = " ".join(message.split())
        if not normalized_message or len(normalized_message) > 500:
            raise GitError("Commit message must contain between 1 and 500 characters")
        review = self.review()
        if review["clean"]:
            raise GitError("There are no changes to commit")
        if review["restricted_changes"]:
            raise GitError(
                "The repository contains changed secret or ignored paths; "
                "handle them outside the Agent"
            )
        if any(file["status"] == "conflicted" for file in review["files"]):
            raise GitError("Resolve merge conflicts before creating a commit")
        intent = registry.create(
            action="commit",
            tenant_id=tenant_id,
            workspace_id=self.workspace.id,
            fingerprint=self.fingerprint(),
            message=normalized_message,
        )
        details = [
            f"Commit {len(review['files'])} changed files",
            f"+{review['additions']} / -{review['deletions']} lines in the current review",
            f'Message: "{normalized_message}"',
            "Git hooks are disabled for this Agent-created commit",
        ]
        return intent, details

    def commit(self, intent: GitIntent) -> dict[str, object]:
        if intent.fingerprint != self.fingerprint():
            raise GitConfirmationError(
                "Repository changed after confirmation was prepared; review it again"
            )
        _, changes, restricted = self.status()
        if restricted:
            raise GitError("Restricted changed paths prevent an Agent-created commit")
        paths = sorted(
            {
                path
                for change in changes
                for path in (change.path, change.previous_path)
                if path is not None
            }
        )
        if not paths:
            raise GitError("There are no changes to commit")
        self._reject_git_filters(paths)
        self._run("add", "--all", "--", *paths, timeout=30)
        result = self._run("commit", "--no-verify", "-m", intent.message or "", timeout=60)
        commit_hash = self._text(self._run("rev-parse", "HEAD"))
        subject = self._text(self._run("show", "-s", "--format=%s", "HEAD"))
        return {
            "commit": commit_hash,
            "subject": subject,
            "files_committed": len(changes),
            "output": self._text(result),
        }

    def _reject_git_filters(self, paths: list[str]) -> None:
        result = self._run("check-attr", "filter", "--", *paths)
        configured = [
            line
            for line in self._text(result).splitlines()
            if not line.endswith(": filter: unspecified")
        ]
        if configured:
            raise GitError(
                "Git clean filters are not supported by the unsandboxed commit path"
            )

    def prepare_push(
        self,
        registry: GitIntentRegistry,
        *,
        tenant_id: str,
    ) -> tuple[GitIntent, list[str]]:
        branch_info, _, _ = self.status()
        branch = branch_info["branch"]
        head = branch_info["head"]
        if not branch or not head:
            raise GitError("Push requires a named branch with at least one commit")
        upstream = branch_info["upstream"]
        set_upstream = upstream is None
        if upstream:
            remote, remote_branch = str(upstream).split("/", 1)
        else:
            remote, remote_branch = "origin", str(branch)
        self._validate_push_remote(remote)
        intent = registry.create(
            action="push",
            tenant_id=tenant_id,
            workspace_id=self.workspace.id,
            fingerprint=str(head),
            remote=remote,
            branch=remote_branch,
            set_upstream=set_upstream,
        )
        details = [
            f"Remote: {remote}",
            f"Local branch: {branch}",
            f"Destination branch: {remote_branch}",
            f"HEAD: {str(head)[:12]}",
            "This is a normal push; force-push is never used",
        ]
        return intent, details

    def _validate_push_remote(self, remote: str) -> None:
        local_helpers = self._run(
            "config", "--local", "--get-all", "credential.helper", allow_failure=True
        )
        if local_helpers.returncode == 0 and self._text(local_helpers):
            raise GitError("Repository-local credential helpers are not allowed")
        result = self._run("remote", "get-url", "--push", remote)
        url = self._text(result)
        lowered = url.casefold()
        safe_scheme = lowered.startswith(("https://", "http://", "ssh://", "git://"))
        safe_scp = ":" in url and "@" in url.split(":", 1)[0]
        if (
            not url
            or url.startswith("-")
            or any(character in url for character in "\r\n\0")
            or lowered.startswith("ext::")
            or not (safe_scheme or safe_scp)
        ):
            raise GitError("Only HTTP(S), SSH, and Git network remotes are supported")

    def push(self, intent: GitIntent) -> dict[str, str]:
        current_head = self._text(self._run("rev-parse", "HEAD"))
        if current_head != intent.fingerprint:
            raise GitConfirmationError(
                "HEAD changed after push confirmation was prepared; confirm the push again"
            )
        assert intent.remote is not None
        assert intent.branch is not None
        self._validate_push_remote(intent.remote)
        arguments = ["push", "--porcelain"]
        if intent.set_upstream:
            arguments.extend(["--set-upstream", intent.remote])
        else:
            arguments.append(intent.remote)
        arguments.append(f"HEAD:refs/heads/{intent.branch}")
        result = self._run(*arguments, timeout=120)
        summary = self._text(result) or result.stderr.decode("utf-8", errors="replace").strip()
        return {
            "remote": intent.remote,
            "branch": intent.branch,
            "head": current_head,
            "summary": summary,
        }
