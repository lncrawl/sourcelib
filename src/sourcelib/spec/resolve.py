"""Resolving ``extends`` into one document, per RFC-0001 section 5.

Merging happens on raw documents *before* validation, which is what lets a two-line alias
spec exist: on its own it declares no stages and would fail the requirements in section
3.3, and only the merged document is expected to satisfy them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sourcelib.spec.loader import parse_yaml
from sourcelib.spec.model import SourceSpec

__all__ = ["MAX_DEPTH", "ResolveError", "merge", "resolve_document", "resolve_file"]

#: RFC-0001 section 5.2 requires a limit and asks for at least this.
MAX_DEPTH = 8

#: Folders an ``extends`` may name. Pointing at `disabled/` would let disabling a spec
#: silently orphan every mirror extending it.
EXTENDABLE = ("specs", "base")


class ResolveError(Exception):
    """A spec could not be resolved: a bad path, a cycle, or too deep a chain."""


def merge(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *child* over *parent* by the rules in RFC-0001 section 5.1."""
    out = dict(parent)
    for key, value in child.items():
        # An explicit null deletes rather than overwrites, which is the only way a child
        # can remove something a parent declared.
        if value is None and key in out:
            del out[key]
            continue

        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = merge(current, value)
        elif key == "fallback" and isinstance(current, list) and isinstance(value, list):
            # Prepending is what makes fallback inheritable: the child's selector is tried
            # first and everything the parent knew still follows.
            out[key] = list(value) + list(current)
        else:
            out[key] = value
    return out


def _repo_path(root: Path, reference: str) -> Path:
    target = (root / reference).resolve()
    # A spec must not be able to read outside the repository (RFC-0001 section 10).
    if not target.is_relative_to(root.resolve()):
        raise ResolveError(f"extends {reference!r} escapes the repository")
    if not target.exists():
        raise ResolveError(f"extends {reference!r} does not exist")
    parts = target.relative_to(root.resolve()).parts
    if not parts or parts[0] not in EXTENDABLE:
        allowed = " or ".join(f"{name}/" for name in EXTENDABLE)
        raise ResolveError(f"extends {reference!r} must name a document in {allowed}")
    return target


def resolve_document(
    document: Dict[str, Any],
    root: Optional[Path] = None,
    origin: Optional[Path] = None,
) -> Dict[str, Any]:
    """Merge *document* with its ancestors and return the raw merged mapping.

    *root* is the repository root, required only when the document extends another.
    """
    chain: List[str] = [str(origin) if origin else "<document>"]
    merged = document
    seen = {origin.resolve()} if origin else set()

    while merged.get("extends"):
        reference = merged["extends"]
        if root is None:
            raise ResolveError(f"cannot resolve extends {reference!r} without a repository root")

        parent_path = _repo_path(Path(root), reference)
        if parent_path in seen:
            raise ResolveError(f"extends forms a cycle: {' -> '.join(chain)} -> {reference}")
        seen.add(parent_path)
        chain.append(reference)

        if len(chain) - 1 > MAX_DEPTH:
            raise ResolveError(f"extends chain deeper than {MAX_DEPTH}: {' -> '.join(chain)}")

        parent = parse_yaml(parent_path.read_text(encoding="utf-8"))
        # The child's own extends is consumed, and the parent's takes its place so the
        # chain keeps walking upward.
        child = {k: v for k, v in merged.items() if k != "extends"}
        merged = merge(parent, child)

    return merged


def resolve_file(path: Path, root: Optional[Path] = None) -> SourceSpec:
    """Read, resolve and validate one spec document."""
    path = Path(path)
    if root is None:
        root = path.parent.parent
    document = parse_yaml(path.read_text(encoding="utf-8"))
    return SourceSpec.model_validate(resolve_document(document, root=Path(root), origin=path))
