from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_product.services.workspace import (
    CodeWorkspace,
    WorkspacePathError,
    WorkspaceRegistry,
)


def make_workspace(tmp_path: Path) -> CodeWorkspace:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "def greet():\n    return 'hello hajimi'\n", encoding="utf-8"
    )
    (root / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    (root / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / ".env.example").write_text("API_KEY=replace-me\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "ignored.js").write_text("secret package", encoding="utf-8")
    return WorkspaceRegistry().create(str(root), "tenant-a")


def test_workspace_lists_reads_and_searches_source_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    listing = workspace.list_files()
    assert listing["files"] == [".env.example", "src/app.py"]

    content = workspace.read_file("src/app.py", 1, 2)
    assert content["content"] == "1: def greet():\n2:     return 'hello hajimi'"
    assert content["complete"] is True
    assert content["sha256"] is not None
    assert "replace-me" in workspace.read_file(".env.example")["content"]

    partial = workspace.read_file("src/app.py", 1, 1)
    assert partial["complete"] is False
    assert partial["sha256"] is None

    search = workspace.search_text("hajimi")
    assert search["matches"] == [
        {"path": "src/app.py", "line_number": 2, "line": "    return 'hello hajimi'"}
    ]


def test_workspace_blocks_path_traversal_and_secret_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(WorkspacePathError, match="outside"):
        workspace.read_file("../outside.txt")
    with pytest.raises(WorkspacePathError, match="secret"):
        workspace.read_file(".env")
    with pytest.raises(WorkspacePathError, match="secret"):
        workspace.read_file(".env.local")
    with pytest.raises(WorkspacePathError, match="ignored"):
        workspace.read_file("node_modules/ignored.js")


def test_workspace_creates_and_updates_utf8_files_with_concurrency_check(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)

    created = workspace.write_file("notes/plan.md", "# Plan\n\nFirst version.\n")
    assert created["action"] == "created"
    assert created["bytes_written"] == len(b"# Plan\n\nFirst version.\n")
    assert workspace.read_file("notes/plan.md")["content"].startswith("1: # Plan")

    current = workspace.read_file("src/app.py")
    updated = workspace.write_file(
        "src/app.py",
        "def greet():\n    return 'hello agent'\n",
        expected_sha256=str(current["sha256"]),
    )
    assert updated["action"] == "updated"
    assert updated["previous_sha256"] == current["sha256"]
    assert "hello agent" in workspace.read_file("src/app.py")["content"]


def test_workspace_rejects_stale_or_unapproved_write_targets(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    with pytest.raises(WorkspacePathError, match="Read the existing file first"):
        workspace.write_file("src/app.py", "overwritten")
    with pytest.raises(WorkspacePathError, match="changed after it was read"):
        workspace.write_file("src/app.py", "overwritten", expected_sha256="0" * 64)
    with pytest.raises(WorkspacePathError, match="secret"):
        workspace.write_file(".env", "TOKEN=replaced")
    with pytest.raises(WorkspacePathError, match="ignored"):
        workspace.write_file(".git/config", "unsafe")
    with pytest.raises(WorkspacePathError, match="outside"):
        workspace.write_file("../outside.txt", "unsafe")


def test_workspace_api_is_tenant_scoped(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    response = client.post(
        "/v1/workspaces",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"path": str(root)},
    )

    assert response.status_code == 201, response.text
    workspace_id = response.json()["id"]
    assert response.json()["name"] == "repository"

    own = client.get(
        f"/v1/workspaces/{workspace_id}", headers={"X-Tenant-ID": "tenant-a"}
    )
    other = client.get(
        f"/v1/workspaces/{workspace_id}", headers={"X-Tenant-ID": "tenant-b"}
    )
    assert own.status_code == 200
    assert other.status_code == 404


def test_chat_rejects_unknown_workspace(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "tenant-a", "X-Workspace-ID": "missing"},
        json={"message": "Inspect the repo"},
    )

    assert response.status_code == 404
