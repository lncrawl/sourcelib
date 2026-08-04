"""This package must not import the crawler.

That single rule is what makes replacing the crawler's core a deletion rather than a
rewrite. It is cheap to hold from the first commit and expensive to restore later, so it is
a test rather than a convention.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "sourcelib"
FORBIDDEN = {"lncrawl", "lightnovel_crawler"}


def _modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_no_crawler_import(path):
    for module in _modules(path):
        assert module.split(".")[0] not in FORBIDDEN, f"{path.name} imports {module}"


def test_the_package_has_sources_to_check():
    assert list(SRC.rglob("*.py"))
