import subprocess
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
