"""Finding the spec that serves a URL.

This lives here rather than in an application because more than one application needs it: a
command line tool and a server both have to answer "which source reads this host", and two
implementations of that question drift. Drift here means one of them serving a source the
other considers disabled.

Manifest sync belongs beside it and is not written yet: fetching changed files needs the HTTP
layer, which arrives with the fetch engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit

import idna

from sourcelib.spec.checks import Problem, check_resolved
from sourcelib.spec.loader import parse_yaml
from sourcelib.spec.model import SourceSpec
from sourcelib.spec.resolve import ResolveError, resolve_document

__all__ = ["Entry", "Registry", "normalise_host"]

#: Where concrete specs live, and whether a spec found there is served.
CONCRETE = {"specs": True, "disabled": False}


def normalise_host(url: str) -> str:
    """The host of *url*, normalised the one way every implementation must agree on.

    RFC-0001 section 8.1: lowercase, no scheme, no userinfo, no port, no trailing slash, no
    leading ``www.``, and IDNA2008 A-label form. The entire layout rests on this deriving
    the same filename everywhere, so it is one function and never inlined.
    """
    text = url.strip()
    if "//" not in text:
        text = f"//{text}"
    host = urlsplit(text).netloc

    if "@" in host:
        host = host.rsplit("@", 1)[1]
    # A bracketed IPv6 literal keeps its colons; anything else splits on the port.
    if host.startswith("["):
        host = host.partition("]")[0] + "]"
    elif ":" in host:
        host = host.rsplit(":", 1)[0]

    host = host.rstrip("/").lower()
    if host.startswith("www."):
        host = host[4:]

    return _to_ascii(host)


def _to_ascii(host: str) -> str:
    if host.isascii():
        return host
    try:
        # IDNA2008 specifically, never the standard library's codec. That one implements
        # IDNA2003, which maps faß.example to fass.example: a different host rather than a
        # different spelling of one. RFC-0001 section 8.1 names the hazard and requires 2008.
        return idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        # An unencodable host cannot name a file, so the caller simply gets no match. It is
        # returned unchanged rather than guessed at.
        return host


class Entry:
    """One concrete spec the registry knows, and whether it is served."""

    __slots__ = ("host", "path", "served", "spec")

    def __init__(self, host: str, path: Path, served: bool, spec: SourceSpec) -> None:
        self.host = host
        self.path = path
        self.served = served
        self.spec = spec

    @property
    def disabled_reason(self) -> Optional[str]:
        return self.spec.disabled

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "served" if self.served else f"disabled: {self.disabled_reason}"
        return f"Entry({self.host!r}, {state})"


class Registry:
    """Every concrete spec in one repository, indexed by host."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._entries: Dict[str, Entry] = {}
        self._problems: List[Tuple[Path, str]] = []

    @classmethod
    def load(cls, root: Path, strict: bool = False) -> "Registry":
        """Read every concrete spec under *root*.

        A document that cannot be read is recorded rather than raised, so one broken file
        does not deny every other host. With *strict* it raises instead.
        """
        registry = cls(root)
        for folder, served in CONCRETE.items():
            for path in sorted((registry.root / folder).glob("*.yaml")):
                try:
                    registry._add(path, served)
                except (ResolveError, ValueError) as error:
                    if strict:
                        raise
                    registry._problems.append((path, str(error)))
        return registry

    def _add(self, path: Path, served: bool) -> None:
        document = parse_yaml(path.read_text(encoding="utf-8"))
        spec = SourceSpec.model_validate(resolve_document(document, root=self.root, origin=path))
        if spec.base_url is None:
            raise ValueError(f"{path.name} is in {path.parent.name}/ but declares no base_url")

        host = normalise_host(spec.base_url)
        if host != path.stem:
            raise ValueError(f"{path.name} declares base_url for {host!r}")
        if host in self._entries:
            raise ValueError(f"{host!r} is claimed by two documents")

        self._entries[host] = Entry(host, path, served, spec)

    def find(self, url: str) -> Optional[Entry]:
        """The entry for *url*'s host, served or not.

        A disabled host returns its entry rather than None. Disabled is an answer, and
        treating it as a miss is what would let a deliberately turned-off host fall through
        to something else.
        """
        return self._entries.get(normalise_host(url))

    def serves(self, url: str) -> bool:
        entry = self.find(url)
        return entry is not None and entry.served

    @property
    def hosts(self) -> List[str]:
        return sorted(self._entries)

    @property
    def served(self) -> List[Entry]:
        return [e for e in self._entries.values() if e.served]

    @property
    def problems(self) -> List[Tuple[Path, str]]:
        """Documents that could not be read, as (path, reason)."""
        return list(self._problems)

    def unservable(self) -> Iterator[Tuple[Entry, List[Problem]]]:
        """Served entries that fail the resolved-spec requirements."""
        for entry in self.served:
            problems = check_resolved(entry.spec)
            if problems:
                yield entry, problems

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, url: object) -> bool:
        return isinstance(url, str) and normalise_host(url) in self._entries
