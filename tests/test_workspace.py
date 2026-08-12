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
    (root / "node_modules").mkdir()
    (root / "node_modules" / "ignored.js").write_text("secret package", encoding="utf-8")
    return WorkspaceRegistry().create(str(root), "tenant-a")


def test_workspace_lists_reads_and_searches_source_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    listing = workspace.list_files()
    assert listing["files"] == ["src/app.py"]

    content = workspace.read_file("src/app.py", 1, 2)
    assert content["content"] == "1: def greet():\n2:     return 'hello hajimi'"

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
