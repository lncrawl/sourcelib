"""Command line entry point.

`check`, `resolve` and `schema` are offline and need nothing but the repository. `try` and
`explain` reach a site, so they import the HTTP layer lazily: a base install has no scraper, and
validating a spec should not require one.

`record`, for writing offline fixtures, is not built yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

import yaml

from sourcelib.spec.checks import check_resolved
from sourcelib.spec.extract import Document
from sourcelib.spec.loader import parse_yaml
from sourcelib.spec.model import SourceSpec
from sourcelib.spec.resolve import ResolveError, resolve_document
from sourcelib.spec.schema import render, write

#: Folders holding documents worth checking, in the order a report should list them.
FOLDERS = ("specs", "disabled", "base")


def _documents(paths: Sequence[Path]) -> List[Path]:
    found: List[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("*.yaml")))
        elif path.exists():
            found.append(path)
    return found


def _repo_root(explicit: Optional[Path], paths: Sequence[Path]) -> Path:
    if explicit is not None:
        return explicit
    # A document lives in <root>/<folder>/<host>.yaml, so its grandparent is the root.
    for path in paths:
        candidate = path if path.is_dir() else path.parent
        if candidate.name in FOLDERS:
            return candidate.parent
    return Path.cwd()


def _check(paths: Sequence[Path], root: Optional[Path], strict: bool) -> int:
    targets = [p for p in paths if p.exists()]
    if not targets:
        print("nothing to check", file=sys.stderr)
        return 1 if strict else 0

    repo = _repo_root(root, targets)
    files = _documents(targets)
    if not files:
        print("no documents found", file=sys.stderr)
        return 1 if strict else 0

    failed = 0
    for file in files:
        try:
            document = parse_yaml(file.read_text(encoding="utf-8"))
            resolved = resolve_document(document, root=repo, origin=file)
            spec = SourceSpec.model_validate(resolved)
        except (ResolveError, yaml.YAMLError, ValueError) as error:
            failed += 1
            print(f"{file}: {error}", file=sys.stderr)
            continue

        problems = check_resolved(spec)
        if problems:
            failed += 1
            for problem in problems:
                print(f"{file}: {problem}", file=sys.stderr)

    print(f"{len(files) - failed} of {len(files)} documents valid")
    return 1 if failed else 0


def _resolve(path: Path, root: Optional[Path], as_json: bool) -> int:
    repo = _repo_root(root, [path])
    try:
        document = parse_yaml(path.read_text(encoding="utf-8"))
        resolved = resolve_document(document, root=repo, origin=path)
    except (ResolveError, yaml.YAMLError, ValueError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(resolved, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True), end="")
    return 0


def _origin_and_rate(path: Path, repo: Path) -> Tuple[str, float]:
    """The host and the pace a spec asks for, resolved through `extends`.

    Both have to come from the *resolved* document. A three-line spec declares neither, and its
    base declares both, so reading the raw file gets the pace of a spec that inherits one wrong.
    Passing no pace at all was worse: `rate_limit` was declared, documented and validated, and then
    every command ignored it. A base that sets it does so because the host challenges bursts.
    """
    try:
        document = parse_yaml(path.read_text(encoding="utf-8"))
        resolved = resolve_document(document, root=repo, origin=path)
        spec = SourceSpec.model_validate(resolved)
    except Exception:  # the trial itself reports a document that will not load
        return "", 0.0
    return str(spec.base_url or ""), float(spec.rate_limit or 0.0)


def _try(
    path: Path,
    url: str,
    root: Optional[Path],
    as_json: bool,
    sample: int,
    toc_pages: Optional[int],
) -> int:
    from sourcelib.http import ScraperFetcher
    from sourcelib.trial import run_trial

    repo = _repo_root(root, [path])
    origin, rate = _origin_and_rate(path, repo)

    with ScraperFetcher(origin=origin, rate_limit=rate) as fetcher:
        trial = run_trial(path, url, fetcher, root=repo, sample=sample, toc_pages=toc_pages)

    return _report(trial, as_json)


def _try_search(path: Path, query: str, root: Optional[Path], as_json: bool) -> int:
    from sourcelib.http import ScraperFetcher
    from sourcelib.trial import run_search_trial

    repo = _repo_root(root, [path])
    origin, rate = _origin_and_rate(path, repo)

    with ScraperFetcher(origin=origin, rate_limit=rate) as fetcher:
        trial = run_search_trial(path, query, fetcher, root=repo)

    return _report(trial, as_json)


def _report(trial: Any, as_json: bool) -> int:
    from sourcelib.trial import format_trial

    if as_json:
        print(json.dumps(trial.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_trial(trial))
    return 0 if trial.ok else 1


def _explain(url: str, as_json: bool, render: bool) -> int:
    from sourcelib.explain import explain, format_digest
    from sourcelib.http import ScraperFetcher

    try:
        with ScraperFetcher(origin=url) as fetcher:
            response = fetcher.render(url) if render else fetcher.fetch("GET", url)
    except Exception as error:
        # This is the first command anyone runs, so a mistyped URL must not answer with a
        # traceback. `try` already reports this way; this did not.
        print(f"could not fetch {url}: {_reason(error)}", file=sys.stderr)
        return 1

    document = Document.from_html(response.text, url=response.url, headers=response.headers)
    digest = explain(document)
    print(
        json.dumps(digest.to_dict(), indent=2, ensure_ascii=False)
        if as_json
        else format_digest(digest)
    )
    return 0


def _reason(error: BaseException) -> str:
    """A one-line account of a failed retrieval, without the traceback."""
    text = str(error).strip()
    return text or type(error).__name__


def _record(path: Path, url: str, root: Optional[Path]) -> int:
    from sourcelib.fixtures import Recording, RecordingFetcher, save
    from sourcelib.http import ScraperFetcher
    from sourcelib.trial import format_trial, run_trial

    repo = _repo_root(root, [path])
    origin, rate = _origin_and_rate(path, repo)
    with ScraperFetcher(origin=origin, rate_limit=rate) as live:
        recorder = RecordingFetcher(live)
        trial = run_trial(path, url, recorder, root=repo)

    if not trial.ok:
        # Recording a spec that does not work would bake the failure into the suite.
        print(format_trial(trial), file=sys.stderr)
        print("not recorded: make `try` pass first", file=sys.stderr)
        return 1

    target = save(repo, path.stem, Recording(url, recorder.pages, trial.summary))
    print(f"recorded {len(recorder.pages)} page(s) to {target}")
    return 0


def _fixtures(root: Optional[Path], strict: bool) -> int:
    from sourcelib.fixtures import hosts, replay

    repo = _repo_root(root, [])
    known = hosts(repo)
    if not known:
        print("no recordings found", file=sys.stderr)
        return 1 if strict else 0

    failed = 0
    for host in known:
        spec = repo / "specs" / f"{host}.yaml"
        if not spec.exists():
            failed += 1
            print(f"{host}: recorded, but specs/{host}.yaml is gone", file=sys.stderr)
            continue
        ok, differences = replay(repo, host, spec)
        if not ok:
            failed += 1
            for line in differences:
                print(f"{host}: {line}", file=sys.stderr)

    print(f"{len(known) - failed} of {len(known)} recordings replay unchanged")
    return 1 if failed else 0


def _schema(output: Optional[Path], check_only: bool) -> int:
    if output is None:
        print(render(), end="")
        return 0
    if check_only:
        if not output.exists() or output.read_text(encoding="utf-8") != render():
            print(f"{output} is out of date; run `sourcelib schema -o {output}`", file=sys.stderr)
            return 1
        return 0
    write(output)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="sourcelib")
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared by the commands that resolve `extends`, and declared on them rather than on
    # the top-level parser so `sourcelib check --root X` works, which is where a reader
    # expects to type it.
    rooted = argparse.ArgumentParser(add_help=False)
    rooted.add_argument("--root", type=Path, help="the repository root; inferred when omitted")

    check = sub.add_parser("check", parents=[rooted], help="resolve and validate documents")
    check.add_argument("paths", nargs="*", type=Path, default=[Path(f) for f in FOLDERS])
    check.add_argument("--strict", action="store_true", help="fail when nothing was found")
    check.add_argument(
        "--fixtures",
        action="store_true",
        help="replay recordings instead, offline. Fixtures test the spec; only a live run "
        "tests the site",
    )

    resolve = sub.add_parser(
        "resolve", parents=[rooted], help="print one document with its ancestors merged"
    )
    resolve.add_argument("path", type=Path)
    resolve.add_argument("--json", action="store_true", help="emit JSON instead of YAML")

    trial = sub.add_parser(
        "try", parents=[rooted], help="run a spec against a live URL and report field by field"
    )
    trial.add_argument("path", type=Path)
    trial.add_argument("url", help="a novel URL on that host")
    trial.add_argument("--json", action="store_true", help="emit JSON instead of text")
    trial.add_argument(
        "--sample",
        type=int,
        default=3,
        metavar="N",
        help="how many chapters to download, spread evenly and always including the first and "
        "last. Three by default. Two would let a broken join across a multi-page body through, "
        "and more crosses more of the boundaries where a theme changes shape",
    )
    trial.add_argument(
        "--toc-pages",
        type=int,
        default=None,
        metavar="N",
        help="walk at most N pages of the chapter list. For iterating on a spec: a `while` or "
        "`next` walk is sequential, so a long list is hundreds of requests before the first "
        "chapter is read. The reported chapter count is then short, and says so",
    )
    searching = sub.add_parser(
        "try-search",
        parents=[rooted],
        help="run a spec's search stage against a live query and report what it returned",
    )
    searching.add_argument("path", type=Path)
    searching.add_argument("query", help="what to search for. Use a title the host really carries")
    searching.add_argument("--json", action="store_true", help="emit JSON instead of text")

    keep = sub.add_parser(
        "record", parents=[rooted], help="save a site's responses so a spec can be tested offline"
    )
    keep.add_argument("path", type=Path)
    keep.add_argument("url", help="a novel URL on that host")

    digest = sub.add_parser(
        "explain", help="a structural digest of a page, for writing a spec against it"
    )
    digest.add_argument("url")
    digest.add_argument("--json", action="store_true", help="emit JSON instead of text")
    digest.add_argument(
        "--render", action="store_true", help="run the page's scripts first, for a JS shell"
    )

    emit = sub.add_parser("schema", help="print or write the JSON Schema")
    emit.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    emit.add_argument(
        "--check",
        action="store_true",
        help="fail if the file at --output differs from the generated schema",
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        if args.fixtures:
            return _fixtures(args.root, args.strict)
        return _check(args.paths, args.root, args.strict)
    if args.command == "resolve":
        return _resolve(args.path, args.root, args.json)
    if args.command == "try":
        return _try(args.path, args.url, args.root, args.json, args.sample, args.toc_pages)

    if args.command == "try-search":
        return _try_search(args.path, args.query, args.root, args.json)
    if args.command == "explain":
        return _explain(args.url, args.json, args.render)
    if args.command == "record":
        return _record(args.path, args.url, args.root)
    if args.command == "schema":
        return _schema(args.output, args.check)
    return 1  # pragma: no cover - argparse rejects an unknown command first


if __name__ == "__main__":
    raise SystemExit(main())
