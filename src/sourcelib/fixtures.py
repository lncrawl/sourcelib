"""Recording a site once so a spec can be tested without it.

A fixture turns spec repair into a deterministic loop: the same bytes every run, no network, no
rate limit, and a diff that means the spec changed rather than the site did. That is what makes
CI able to say a pull request broke something.

It is deliberately **not** a substitute for testing against the live site. A recorded page
eventually stops resembling the real one, so a green fixture suite can mask a dead source.
Fixtures test the spec; only a live run tests the site.

RFC-0001 leaves this format to the implementation, so it is one gzipped JSON document per host:
the responses, and the result they produced when the spec was known good.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sourcelib.fetch import Fetched, Fetcher
from sourcelib.trial import COMPARED

__all__ = [
    "FIXTURE_NAME",
    "Recording",
    "RecordingFetcher",
    "ReplayFetcher",
    "compare",
    "hosts",
    "load",
    "path_for",
    "replay",
    "save",
]

#: One file per host. Gzipped because a recorded page is mostly repetitive markup.
FIXTURE_NAME = "recording.json.gz"


class Recording:
    """Responses captured from one host, and the result the spec produced from them."""

    __slots__ = ("url", "pages", "expected")

    def __init__(
        self,
        url: str = "",
        pages: Optional[Dict[str, Dict[str, Any]]] = None,
        expected: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.url = url
        self.pages = pages or {}
        self.expected = expected or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"url": self.url, "pages": self.pages, "expected": self.expected}

    def fetcher(self) -> "ReplayFetcher":
        return ReplayFetcher(self.pages)


class RecordingFetcher:
    """Wraps a Fetcher and keeps everything it retrieved."""

    def __init__(self, inner: Fetcher) -> None:
        self.inner = inner
        self.pages: Dict[str, Dict[str, Any]] = {}

    def fetch(self, method: str, url: str, **options: Any) -> Fetched:
        response = self.inner.fetch(method, url, **options)
        self._keep(method, url, response)
        return response

    def render(self, url: str, *, wait_for: Optional[str] = None) -> Fetched:
        response = self.inner.render(url, wait_for=wait_for)
        self._keep("RENDER", url, response)
        return response

    def _keep(self, method: str, url: str, response: Fetched) -> None:
        # Keyed by the requested URL rather than the final one, because replay looks up what
        # the spec asked for, not where a redirect landed.
        self.pages[url] = {
            "method": method,
            "final_url": response.url,
            "status": response.status,
            "headers": dict(response.headers),
            "text": response.text,
        }


class ReplayFetcher:
    """Answers from a recording, and says plainly when a spec asks for something unrecorded."""

    def __init__(self, pages: Dict[str, Dict[str, Any]]) -> None:
        self.pages = pages
        self.missing: List[str] = []

    def fetch(self, method: str, url: str, **options: Any) -> Fetched:
        entry = self.pages.get(url)
        if entry is None:
            self.missing.append(url)
            # A spec reaching a URL the recording does not hold usually means the spec changed
            # its request, which is worth reporting rather than silently passing.
            from sourcelib.fetch import FetchError

            raise FetchError(
                f"not in the recording: {url}. Re-record if the spec changed on purpose"
            )
        return Fetched(
            url=entry.get("final_url") or url,
            text=entry.get("text", ""),
            headers=entry.get("headers") or {},
            status=int(entry.get("status", 200)),
        )

    def render(self, url: str, *, wait_for: Optional[str] = None) -> Fetched:
        return self.fetch("GET", url)


def path_for(root: Path, host: str) -> Path:
    return Path(root) / "fixtures" / host / FIXTURE_NAME


def save(root: Path, host: str, recording: Recording) -> Path:
    target = path_for(root, host)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(recording.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    # mtime=0 so re-recording identical bytes produces an identical file rather than a diff.
    with gzip.GzipFile(filename=str(target), mode="wb", mtime=0) as handle:
        handle.write(payload)
    return target


def load(root: Path, host: str) -> Recording:
    target = path_for(root, host)
    with gzip.open(target, "rb") as handle:
        data = json.loads(handle.read().decode("utf-8"))
    return Recording(
        url=data.get("url", ""),
        pages=data.get("pages") or {},
        expected=data.get("expected") or {},
    )


def hosts(root: Path) -> List[str]:
    """Every host with a recording, in order."""
    folder = Path(root) / "fixtures"
    if not folder.is_dir():
        return []
    return sorted(p.parent.name for p in folder.glob(f"*/{FIXTURE_NAME}"))


def compare(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    """Every difference between a recorded result and a fresh one, as readable lines."""
    differences: List[str] = []

    for name in (*COMPARED, "chapters", "volumes", "first_chapter", "last_chapter"):
        if name not in expected:
            continue
        was, now = expected[name], actual.get(name)
        if was != now:
            differences.append(f"{name}: expected {_short(was)}, got {_short(now)}")

    for was, now in zip(expected.get("bodies") or [], actual.get("bodies") or []):
        if was.get("characters") != now.get("characters"):
            differences.append(
                f"chapter {was.get('id')}: body was {was.get('characters')} characters, "
                f"now {now.get('characters')}"
            )
    return differences


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else repr(value)
    return text if len(text) <= 60 else text[:59] + "…"


def replay(root: Path, host: str, spec_path: Path, sample: int = 3) -> Tuple[bool, List[str]]:
    """Run a spec against its recording and report what differs."""
    from sourcelib.trial import run_trial

    recording = load(root, host)
    trial = run_trial(spec_path, recording.url, recording.fetcher(), root=root, sample=sample)
    if trial.error:
        return False, [trial.error]
    differences = compare(recording.expected, trial.summary)
    return not differences, differences
