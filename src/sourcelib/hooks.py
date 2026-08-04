"""The escape hatch, per RFC-0001 sections 7 and 10.

Some sites cannot be described as data: an encrypted chapter body, a token handshake, a request
signature. A hook is Python for exactly those, and everything about how it is loaded exists to
keep it small and safe to share.

The context is **passed as a parameter, never looked up**. A hook module is imported once and
shared by every crawl of every host that references it, so a module global is neither per-crawl
nor per-thread, and a hook caching a token in one hands one novel another's credentials under
load. Nothing here exposes an ambient context, which is what makes that mistake impossible
rather than merely discouraged.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple

from sourcelib.spec.model import hook_points

__all__ = [
    "Context",
    "HookError",
    "HookRegistry",
    "State",
    "function_name",
]

#: Directories a hook may be imported from by name, so their filenames must be identifiers.
IMPORTABLE = ("lib", "shared")

#: Reaching into another host's implementation couples two sources with no relationship.
FORBIDDEN_IMPORT = "hooks.sites"


class HookError(Exception):
    """A hook could not be loaded, or does not define the point a spec bound to it."""


def function_name(point: str) -> str:
    """The function a point binds to: the point with its separator as an underscore.

    A point is dotted and a function name cannot be, so `chapter.body` binds to `chapter_body`.
    """
    return point.replace(".", "_")


class State:
    """Scratch shared between a crawl's hooks: concurrency-safe, untyped, never persisted.

    Deliberately not modelled the way `Novel` and `Chapter` are. Those are output and get
    serialized and compared across versions; this never leaves the process and is discarded
    when the crawl ends, so typing it would constrain hooks without protecting anything.

    It must accept values that are not serializable. A hook speaking a binary protocol keeps a
    live client in here, and any structure that normalised values for storage would corrupt
    exactly the entries that most need sharing.
    """

    def __init__(self) -> None:
        self._values: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = value

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._values[key]

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._values

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._values))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Read or create in one step, so two threads cannot both create."""
        with self._lock:
            return self._values.setdefault(key, default)

    def update_atomically(self, key: str, produce: Callable[[Any], Any]) -> Any:
        """Replace `state[key]` with `produce(current)` while holding the lock.

        The alternative is a hook doing read-modify-write across two calls, which is a race
        every chapter thread can lose.
        """
        with self._lock:
            new = produce(self._values.get(key))
            self._values[key] = new
            return new

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)


class Context:
    """What a hook is given. One per crawl, held by that crawl's interpreter."""

    __slots__ = ("session", "spec", "vars", "state", "owner")

    def __init__(
        self,
        session: Any,
        spec: Any,
        variables: Mapping[str, Any],
        state: Optional[State] = None,
        owner: str = "",
    ) -> None:
        self.session = session
        self.spec = spec
        #: The same values templates see, already evaluated and cached.
        self.vars = variables
        #: Typed as State rather than MutableMapping on purpose: inheriting the abstract
        #: mixins would supply a `pop` that reads and deletes without holding the lock.
        self.state: State = state if state is not None else State()
        #: The stem of the hook file being called, so a hook can namespace its state keys
        #: without hardcoding its own filename.
        self.owner = owner

    def key(self, name: str) -> str:
        """A state key owned by the calling hook.

        A spec may reference several hooks and a `shared/` hook may serve many specs, so two
        independent hooks reaching for `state["token"]` is a collision between files that have
        never seen each other.
        """
        return f"{self.owner}/{name}" if self.owner else name

    def for_owner(self, owner: str) -> "Context":
        """The same crawl's context, labelled with the hook about to be called."""
        return Context(self.session, self.spec, self.vars, self.state, owner)  # type: ignore[arg-type]


class HookRegistry:
    """Loads hook files and binds their functions to points.

    Files are loaded **by path**. A file in `hooks/sites/` is named for its host, so its name
    usually is not a valid identifier: `wtr-lab.com.py` has both a hyphen and dots. Files in
    `hooks/lib/` and `hooks/shared/` are imported by name and so must be identifiers.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._modules: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._prepared = False

    # -- loading ---------------------------------------------------------------------- #

    def _prepare(self) -> None:
        """Make `hooks.lib` and `hooks.shared` importable, once."""
        if self._prepared:
            return
        parent = str(self.root)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        self._prepared = True

    def _resolve(self, reference: str) -> Path:
        target = (self.root / reference).resolve()
        # A spec must not read outside the repository (section 10).
        if not target.is_relative_to(self.root):
            raise HookError(f"hook path {reference!r} escapes the repository")
        if not target.exists():
            raise HookError(f"hook path {reference!r} does not exist")
        if target.suffix != ".py":
            raise HookError(f"hook path {reference!r} is not a Python file")
        return target

    def load(self, reference: str) -> Any:
        """Import one hook file, at most once per registry."""
        with self._lock:
            if reference in self._modules:
                return self._modules[reference]

            path = self._resolve(reference)
            # Before executing, not after: a forbidden import would already have run.
            self._check_imports(reference, path)

            self._prepare()
            name = "sourcelib_hook_" + reference.replace("/", "_").replace(".", "_")
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:  # pragma: no cover - unreadable file
                raise HookError(f"hook file {reference!r} could not be loaded")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as error:
                raise HookError(f"hook file {reference!r} failed to import: {error}") from error

            self._modules[reference] = module
            return module

    def _check_imports(self, reference: str, path: Path) -> None:
        """Refuse a hook importing another host's implementation.

        Read from the syntax tree rather than the text, so a mention in a comment or a string is
        not a false positive and an aliased import is not a false negative.
        """
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:  # pragma: no cover - exec_module reports this first
            raise HookError(f"hook file {reference!r} does not parse: {error}") from error

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == FORBIDDEN_IMPORT or name.startswith(FORBIDDEN_IMPORT + "."):
                    raise HookError(
                        f"hook {reference!r} imports {name!r}. Reaching into another host's "
                        "implementation couples two sources that have no relationship, so "
                        "behaviour worth sharing belongs in hooks/lib/ or hooks/shared/"
                    )

    # -- binding ---------------------------------------------------------------------- #

    def bind(self, declared: Any) -> Dict[str, Tuple[Callable[..., Any], str]]:
        """Resolve a spec's `hooks` into point -> (function, owner).

        A bare path binds every point the file defines, which is why a spec for a hostile site
        can name one file instead of listing eight points.
        """
        legal = hook_points()

        if isinstance(declared, str):
            module = self.load(declared)
            owner = Path(declared).stem
            found = {}
            for point in legal:
                function = getattr(module, function_name(point), None)
                if callable(function):
                    found[point] = (function, owner)
            if not found:
                raise HookError(
                    f"hook file {declared!r} defines none of the hook points. Name each function "
                    f"after the point it serves, as in {function_name('chapter.body')}"
                )
            return found

        bound: Dict[str, Tuple[Callable[..., Any], str]] = {}
        for point, reference in (declared or {}).items():
            if point not in legal:
                raise HookError(f"unknown hook point {point!r}")
            module = self.load(reference)
            function = getattr(module, function_name(point), None)
            if not callable(function):
                raise HookError(
                    f"{reference!r} does not define {function_name(point)}, which {point!r} "
                    "binds to"
                )
            bound[point] = (function, Path(reference).stem)
        return bound
