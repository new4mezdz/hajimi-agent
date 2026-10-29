import shutil
from pathlib import Path

import pytest

from agent_product.services.support_eval import ignore_knowledge_symlinks


def test_callback_excludes_marked_links_before_copying(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "policy.md").write_text("public policy", encoding="utf-8")
    (source / "linked.md").write_text("outside fixture", encoding="utf-8")
    (source / "linked-folder").mkdir()
    (source / "linked-folder" / "hidden.md").write_text("outside fixture", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path.name in {"linked.md", "linked-folder"} or original(path),
    )
    target = tmp_path / "copied"
    shutil.copytree(source, target, ignore=ignore_knowledge_symlinks)
    assert (target / "policy.md").read_text(encoding="utf-8") == "public policy"
    assert not (target / "linked.md").exists()
    assert not (target / "linked-folder").exists()


@pytest.mark.parametrize("directory", [False, True])
def test_real_symbolic_link_is_not_dereferenced(tmp_path, directory):
    source = tmp_path / "source"
    source.mkdir()
    (source / "policy.md").write_text("public policy", encoding="utf-8")
    external = tmp_path / ("outside-directory" if directory else "outside.md")
    if directory:
        external.mkdir()
        (external / "outside.md").write_text("outside fixture", encoding="utf-8")
    else:
        external.write_text("outside fixture", encoding="utf-8")
    link = source / ("linked-folder" if directory else "linked.md")
    try:
        link.symlink_to(external, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")
    target = tmp_path / "copied"
    shutil.copytree(source, target, ignore=ignore_knowledge_symlinks)
    assert list(path.name for path in target.iterdir()) == ["policy.md"]
