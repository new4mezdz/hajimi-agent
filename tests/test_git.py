import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_product.services.git import (
    GitConfirmationError,
    GitError,
    GitIntentRegistry,
    GitService,
)
from agent_product.services.workspace import WorkspaceRegistry


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def make_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "Hajimi Test")
    git(root, "config", "user.email", "hajimi@example.test")
    (root / "app.py").write_bytes(b"print('one')\n")
    git(root, "add", "app.py")
    git(root, "commit", "-m", "initial")
    return root


def make_service(root: Path) -> GitService:
    workspace = WorkspaceRegistry().create(str(root), "tenant-a")
    return GitService(workspace)


def use_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, system: bytes) -> None:
    system_config = tmp_path / "system.gitconfig"
    system_config.write_bytes(system)
    global_config = tmp_path / "global.gitconfig"
    global_config.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system_config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))


class RequireCredentials(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="hajimi"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_review_combines_modified_staged_and_untracked_files(tmp_path: Path) -> None:
    root = make_repository(tmp_path)
    (root / "app.py").write_bytes(b"print('two')\n")
    (root / "notes.md").write_bytes(b"# Notes\n")
    git(root, "add", "app.py")

    review = make_service(root).review()

    assert review["branch"] == "main"
    assert review["clean"] is False
    assert [file["path"] for file in review["files"]] == ["app.py", "notes.md"]
    assert review["files"][0]["staged"] is True
    assert review["files"][1]["status"] == "untracked"
    assert "+# Notes" in review["files"][1]["diff"]
    assert review["checks"][0]["status"] == "passed"
    assert review["checks"][1]["status"] == "not_run"


def test_commit_requires_fresh_one_time_confirmation(tmp_path: Path) -> None:
    root = make_repository(tmp_path)
    (root / "app.py").write_bytes(b"print('two')\n")
    service = make_service(root)
    registry = GitIntentRegistry()

    stale, _ = service.prepare_commit(
        registry,
        tenant_id="tenant-a",
        message="Update app",
    )
    (root / "app.py").write_bytes(b"print('three')\n")
    consumed = registry.consume(
        stale.id,
        action="commit",
        tenant_id="tenant-a",
        workspace_id=service.workspace.id,
    )
    with pytest.raises(GitConfirmationError, match="changed after"):
        service.commit(consumed)

    intent, _ = service.prepare_commit(
        registry,
        tenant_id="tenant-a",
        message="Update app",
    )
    consumed = registry.consume(
        intent.id,
        action="commit",
        tenant_id="tenant-a",
        workspace_id=service.workspace.id,
    )
    committed = service.commit(consumed)

    assert committed["subject"] == "Update app"
    assert git(root, "status", "--porcelain") == ""
    with pytest.raises(GitConfirmationError, match="already used"):
        registry.consume(
            intent.id,
            action="commit",
            tenant_id="tenant-a",
            workspace_id=service.workspace.id,
        )


def test_commit_rejects_restricted_changed_paths(tmp_path: Path) -> None:
    root = make_repository(tmp_path)
    (root / ".env").write_bytes(b"SECRET=value\n")
    service = make_service(root)

    with pytest.raises(GitError, match="secret or ignored"):
        service.prepare_commit(
            GitIntentRegistry(),
            tenant_id="tenant-a",
            message="Unsafe commit",
        )


def test_push_prepare_rejects_unsafe_remote_helpers(tmp_path: Path) -> None:
    root = make_repository(tmp_path)
    git(root, "remote", "add", "origin", "ext::powershell -Command calc")

    with pytest.raises(GitError, match="network remotes"):
        make_service(root).prepare_push(
            GitIntentRegistry(),
            tenant_id="tenant-a",
        )


def test_review_and_commit_follow_system_line_ending_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_git_config(tmp_path, monkeypatch, system=b"[core]\n\tautocrlf = true\n")
    root = make_repository(tmp_path)
    git(root, "config", "--unset", "core.autocrlf")
    (root / "app.py").write_bytes(b"print('one')\r\nprint('two')\r\n")
    service = make_service(root)

    review = service.review()

    assert [(file["additions"], file["deletions"]) for file in review["files"]] == [(1, 0)]
    assert review["checks"][0]["status"] == "passed"

    registry = GitIntentRegistry()
    intent, _ = service.prepare_commit(registry, tenant_id="tenant-a", message="Add line")
    consumed = registry.consume(
        intent.id,
        action="commit",
        tenant_id="tenant-a",
        workspace_id=service.workspace.id,
    )
    service.commit(consumed)

    assert git(root, "cat-file", "blob", "HEAD:app.py") == "print('one')\nprint('two')"


def test_push_uses_system_credential_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_git_config(
        tmp_path,
        monkeypatch,
        system=b"[credential]\n\thelper = !sh .git/credential-helper.sh\n",
    )
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    root = make_repository(tmp_path)
    (root / ".git" / "credential-helper.sh").write_bytes(
        b'echo "$1" >> .git/credential-requests.log\n'
        b"echo username=hajimi\n"
        b"echo password=not-a-secret\n"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), RequireCredentials)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        git(root, "remote", "add", "origin", f"http://127.0.0.1:{server.server_port}/repo.git")
        service = make_service(root)
        registry = GitIntentRegistry()
        intent, _ = service.prepare_push(registry, tenant_id="tenant-a")
        consumed = registry.consume(
            intent.id,
            action="push",
            tenant_id="tenant-a",
            workspace_id=service.workspace.id,
        )
        with pytest.raises(GitError, match="Authentication failed"):
            service.push(consumed)
    finally:
        server.shutdown()
        server.server_close()

    requests = (root / ".git" / "credential-requests.log").read_text(encoding="utf-8")
    assert requests.split()[0] == "get"


def test_git_review_api_is_workspace_and_tenant_scoped(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = make_repository(tmp_path)
    (root / "app.py").write_bytes(b"print('changed')\n")
    created = client.post(
        "/v1/workspaces",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"path": str(root)},
    )
    workspace_id = created.json()["id"]

    own = client.get(
        f"/v1/workspaces/{workspace_id}/git/review",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    other = client.get(
        f"/v1/workspaces/{workspace_id}/git/review",
        headers={"X-Tenant-ID": "tenant-b"},
    )

    assert own.status_code == 200, own.text
    assert own.json()["files"][0]["path"] == "app.py"
    assert other.status_code == 404


def test_git_commit_api_requires_explicit_confirmation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = make_repository(tmp_path)
    initial_head = git(root, "rev-parse", "HEAD")
    (root / "app.py").write_bytes(b"print('confirmed')\n")
    created = client.post(
        "/v1/workspaces",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"path": str(root)},
    )
    workspace_id = created.json()["id"]
    headers = {"X-Tenant-ID": "tenant-a"}

    prepared = client.post(
        f"/v1/workspaces/{workspace_id}/git/commit/prepare",
        headers=headers,
        json={"message": "Confirmed commit"},
    )

    assert prepared.status_code == 200, prepared.text
    assert git(root, "rev-parse", "HEAD") == initial_head
    confirmation_id = prepared.json()["confirmation_id"]

    confirmed = client.post(
        f"/v1/workspaces/{workspace_id}/git/commit",
        headers=headers,
        json={"confirmation_id": confirmation_id},
    )
    reused = client.post(
        f"/v1/workspaces/{workspace_id}/git/commit",
        headers=headers,
        json={"confirmation_id": confirmation_id},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["subject"] == "Confirmed commit"
    assert git(root, "rev-parse", "HEAD") != initial_head
    assert reused.status_code == 409
