import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import agent_product.services.workspace as workspace_module
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


def test_workspace_raw_read_preserves_exact_line_endings(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    exact = b"first\r\nsecond\r\nthird"
    (workspace.root / "exact.txt").write_bytes(exact)

    result = workspace.read_file("exact.txt", 1, 2, raw=True)

    assert result["content"] == "first\r\nsecond\r\n"
    assert result["format"] == "raw"
    assert result["complete"] is False


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


def test_workspace_listing_skips_file_symlinks(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    secret_alias = workspace.root / "secret-alias.txt"
    source_alias = workspace.root / "source-alias.py"
    try:
        secret_alias.symlink_to(workspace.root / ".env")
        source_alias.symlink_to(workspace.root / "src" / "app.py")
    except (NotImplementedError, OSError):
        pytest.skip("File symlinks are unavailable on this platform")

    listing = workspace.list_files()

    assert "secret-alias.txt" not in listing["files"]
    assert "source-alias.py" not in listing["files"]
    assert workspace.search_text("API_KEY=secret")["matches"] == []


def test_workspace_detects_windows_reparse_points() -> None:
    class ReparsePath:
        def __init__(self, tag: int) -> None:
            self.tag = tag

        @staticmethod
        def is_symlink() -> bool:
            return False

        def lstat(self):
            return type(
                "FakeStat",
                (),
                {"st_file_attributes": 0x400, "st_reparse_tag": self.tag},
            )()

    assert workspace_module._is_link_or_reparse_point(ReparsePath(0xA0000003)) is True
    assert workspace_module._is_link_or_reparse_point(ReparsePath(0x9000001A)) is False


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


def test_workspace_create_file_never_overwrites_existing_content(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    created = workspace.create_file("notes/plan.md", "# Plan\n")

    assert created["action"] == "created"
    assert created["bytes_written"] == len(b"# Plan\n")
    assert (workspace.root / "notes" / "plan.md").read_text(encoding="utf-8") == "# Plan\n"

    with pytest.raises(WorkspacePathError, match="already exists"):
        workspace.create_file("notes/plan.md", "replacement")
    assert (workspace.root / "notes" / "plan.md").read_text(encoding="utf-8") == "# Plan\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows uses MoveFileW instead of hard links")
def test_workspace_create_does_not_depend_on_hard_links_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)

    def unsupported_link(_source: Path, _destination: Path) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)
    created = workspace.create_file("portable.txt", "portable\n")

    assert created["action"] == "created"
    assert (workspace.root / "portable.txt").read_bytes() == b"portable\n"


def test_workspace_serializes_competing_creates_and_patches(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_barrier = Barrier(2)

    def create(content: str) -> bool:
        create_barrier.wait()
        try:
            workspace.create_file("winner.txt", content)
        except WorkspacePathError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_results = list(executor.map(create, ["first", "second"]))
    assert sorted(create_results) == [False, True]
    assert (workspace.root / "winner.txt").read_text(encoding="utf-8") in {
        "first",
        "second",
    }

    target = workspace.root / "counter.txt"
    target.write_text("value = 0\n", encoding="utf-8")
    expected_sha256 = str(workspace.read_file("counter.txt")["sha256"])
    patch_barrier = Barrier(2)

    def patch(replacement: str) -> bool:
        patch_barrier.wait()
        try:
            workspace.apply_patch(
                "counter.txt",
                "value = 0",
                replacement,
                expected_sha256=expected_sha256,
            )
        except WorkspacePathError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        patch_results = list(executor.map(patch, ["value = 1", "value = 2"]))
    assert sorted(patch_results) == [False, True]


def test_workspace_registrations_for_the_same_root_share_write_serialization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    target = root / "value.txt"
    target.write_text("value = 0\n", encoding="utf-8")
    registry = WorkspaceRegistry()
    first = registry.create(str(root), "tenant-a")
    second = registry.create(str(root), "tenant-a")
    restarted = WorkspaceRegistry().create(str(root), "tenant-a")
    other_tenant = WorkspaceRegistry().create(str(root), "tenant-b")
    assert first.id == second.id == restarted.id
    assert other_tenant.id != first.id
    expected_sha256 = str(first.read_file("value.txt")["sha256"])
    barrier = Barrier(2)

    def patch(arguments: tuple[CodeWorkspace, str]) -> bool:
        workspace, replacement = arguments
        barrier.wait()
        try:
            workspace.apply_patch(
                "value.txt",
                "value = 0",
                replacement,
                expected_sha256=expected_sha256,
            )
        except WorkspacePathError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(patch, [(first, "value = 1"), (second, "value = 2")])
        )

    assert sorted(results) == [False, True]

def test_workspace_applies_one_exact_patch_and_preserves_other_content(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    current = workspace.read_file("src/app.py")
    raw_content = str(workspace.read_file("src/app.py", raw=True)["content"])
    line_ending = "\r\n" if "\r\n" in raw_content else "\n"

    patched = workspace.apply_patch(
        "src/app.py",
        f"    return 'hello hajimi'{line_ending}",
        f"    return 'hello agent'{line_ending}",
        expected_sha256=str(current["sha256"]),
    )

    assert patched["action"] == "patched"
    assert patched["previous_sha256"] == current["sha256"]
    assert (workspace.root / "src" / "app.py").read_text(encoding="utf-8") == (
        "def greet():\n    return 'hello agent'\n"
    )


def test_workspace_patch_preserves_mixed_line_endings_and_file_mode(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    target = workspace.root / "mixed.txt"
    target.write_bytes(b"first\r\nold\nlast\r\n")
    old_timestamp = 1_000_000
    os.utime(target, (old_timestamp, old_timestamp))
    if os.name != "nt":
        target.chmod(0o6755)

    workspace.apply_patch("mixed.txt", "old\n", "new\n")

    assert target.read_bytes() == b"first\r\nnew\nlast\r\n"
    assert target.stat().st_mtime > old_timestamp
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_workspace_patch_rejects_ambiguous_missing_or_stale_text(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    duplicate = workspace.root / "duplicate.txt"
    duplicate.write_text("same\nsame\n", encoding="utf-8")
    overlap = workspace.root / "overlap.txt"
    overlap.write_text("aaa", encoding="utf-8")

    with pytest.raises(WorkspacePathError, match="more than once"):
        workspace.apply_patch("duplicate.txt", "same", "different")
    with pytest.raises(WorkspacePathError, match="more than once"):
        workspace.apply_patch("overlap.txt", "aa", "different")
    with pytest.raises(WorkspacePathError, match="not found"):
        workspace.apply_patch("src/app.py", "missing text", "replacement")
    with pytest.raises(WorkspacePathError, match="changed after it was read"):
        workspace.apply_patch(
            "src/app.py",
            "hello hajimi",
            "hello agent",
            expected_sha256="0" * 64,
        )


def test_workspace_new_writes_reuse_path_and_size_boundaries(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    for unsafe_path in ("../outside.txt", ".GIT/config", "data.txt:secret", "NUL.txt"):
        with pytest.raises(WorkspacePathError):
            workspace.create_file(unsafe_path, "unsafe")
    with pytest.raises(WorkspacePathError, match="500 KB"):
        workspace.create_file("too-large.txt", "x" * 500_001)
    with pytest.raises(WorkspacePathError, match="secret"):
        workspace.apply_patch(".env", "secret", "changed")
    with pytest.raises(WorkspacePathError, match="valid UTF-8"):
        workspace.create_file("invalid.txt", "\ud800")
    with pytest.raises(WorkspacePathError, match="valid UTF-8"):
        workspace.apply_patch("src/app.py", "hello hajimi", "\ud800")


def test_workspace_rejects_unapproved_or_oversized_existing_write_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    target = workspace.root / "large.txt"
    target.write_bytes(b"x" * 1_000_001)

    def unexpected_hash(_path: Path) -> str:
        pytest.fail("write_file should reject before hashing the target")

    monkeypatch.setattr(workspace_module, "_sha256_file", unexpected_hash)
    with pytest.raises(WorkspacePathError, match="Read the existing file first"):
        workspace.write_file("large.txt", "replacement")
    with pytest.raises(WorkspacePathError, match="larger than the 1 MB"):
        workspace.write_file("large.txt", "replacement", expected_sha256="0" * 64)


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
