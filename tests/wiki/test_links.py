"""W1.5 — Bidirectional backlink computation tests."""
from __future__ import annotations

from engine.wiki.links import (
    compute_backlinks,
    find_broken_links,
    scan_links_in_file,
)


def test_scan_extracts_markdown_links(tmp_path):
    src = tmp_path / "src.md"
    src.write_text(
        "See [A](./a.md) and [B](../b.md) and a [http link](https://x.com)\n"
        "Also [code](src/x.py#L5).\n",
        encoding="utf-8",
    )
    links = scan_links_in_file(src)
    # Relative .md links are captured; absolute URLs and non-.md refs are filtered out
    targets = sorted(links)
    assert "./a.md" in targets
    assert "../b.md" in targets
    # http link NOT captured (we only care about wiki cross-tier markdown)
    assert "https://x.com" not in targets


def test_compute_backlinks_basic(tmp_path):
    """A page that links to another should show up as a backlink."""
    (tmp_path / "a.md").write_text("See [B](./b.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    backlinks = compute_backlinks(tmp_path)
    # b.md's backlink set should contain a.md
    b_path = (tmp_path / "b.md").resolve()
    a_path = (tmp_path / "a.md").resolve()
    assert b_path in backlinks
    assert a_path in backlinks[b_path]


def test_compute_backlinks_handles_relative_paths(tmp_path):
    """Cross-directory links resolve correctly."""
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "x" / "src.md").write_text("[T](../y/target.md)\n", encoding="utf-8")
    (tmp_path / "y" / "target.md").write_text("# T\n", encoding="utf-8")
    backlinks = compute_backlinks(tmp_path)
    target = (tmp_path / "y" / "target.md").resolve()
    src = (tmp_path / "x" / "src.md").resolve()
    assert target in backlinks
    assert src in backlinks[target]


def test_compute_backlinks_multiple_sources(tmp_path):
    """A page linked from multiple sources lists all backlinks."""
    (tmp_path / "target.md").write_text("# T\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("[T](./target.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("[T](./target.md)\n", encoding="utf-8")
    backlinks = compute_backlinks(tmp_path)
    target = (tmp_path / "target.md").resolve()
    assert len(backlinks.get(target, set())) == 2


def test_find_broken_links_flags_missing_targets(tmp_path):
    """When a markdown link points to a non-existent .md file, flag it."""
    (tmp_path / "a.md").write_text("[X](./missing.md)\n", encoding="utf-8")
    broken = find_broken_links(tmp_path)
    assert broken
    src, target = broken[0]
    assert src == (tmp_path / "a.md").resolve()
    assert target.name == "missing.md"


def test_find_broken_links_empty_when_all_valid(tmp_path):
    (tmp_path / "a.md").write_text("[B](./b.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    broken = find_broken_links(tmp_path)
    assert broken == []
