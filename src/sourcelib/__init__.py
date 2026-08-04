"""Interpreter for declarative light-novel source definitions.

A source definition describes how to read one website as data rather than as code: one
YAML document per host, validated against a published schema and interpreted at runtime.
RFC-0001 in the `lncrawl/sources` repository is the normative definition of the format,
and this package is written against it.

This package never imports the crawler. It depends on the scraper and on nothing else of
ours, which is what makes it the crawler's next core rather than a feature of it.
"""

from importlib.metadata import PackageNotFoundError, version

from sourcelib.registry import Entry, Registry, normalise_host
from sourcelib.spec import (
    ChapterStage,
    Extractor,
    ItemList,
    NovelStage,
    Paginate,
    Request,
    SearchStage,
    SourceSpec,
    TocStage,
    Var,
    hook_points,
)

try:
    __version__ = version("lncrawl-sourcelib")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

__all__ = [
    "ChapterStage",
    "Entry",
    "Extractor",
    "ItemList",
    "NovelStage",
    "Paginate",
    "Registry",
    "Request",
    "SearchStage",
    "SourceSpec",
    "TocStage",
    "Var",
    "hook_points",
    "normalise_host",
    "__version__",
]
