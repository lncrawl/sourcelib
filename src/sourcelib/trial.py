"""Running a spec against a live URL and reporting what each field produced.

This is what makes repairing a source a red/green loop rather than a manual read of a stack
trace, for a person or a model. Two things follow from that and shape the whole module.

Output is **structured first and formatted second**. A reviewer reads the text, but a model and
the web editor read the JSON, and a plaintext protocol that has to be reverse-parsed with
regular expressions is what this replaces.

And a failure names the **spec field**, its file and its line. `toc.items.url matched 0 nodes`
tells you where to look; a parser traceback does not.

Chapters are sampled first, middle and last. First-and-last alone is exactly the sampling that
lets a broken `join` through on a body split across pages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sourcelib.fetch import Fetcher
from sourcelib.models import Chapter, Novel
from sourcelib.runtime import CrawlError, Interpreter, Report
from sourcelib.spec.lines import line_map, locate
from sourcelib.spec.loader import parse_yaml
from sourcelib.spec.model import SourceSpec
from sourcelib.spec.resolve import resolve_document

__all__ = ["COMPARED", "Finding", "Trial", "format_trial", "run_trial", "summarise"]

#: How much of a value to show. Enough to tell right from wrong, short enough to scan.
PREVIEW = 160


@dataclass
class Finding:
    """One thing a run learned about one field.

    `required` is what separates a failure from a warning. Section 4.4 makes a missing cover,
    author, tag list or synopsis a warning, because real pages omit them often enough that
    failing would reject working sources.
    """

    field: str
    ok: bool
    required: bool = True
    detail: str = ""
    count: Optional[int] = None
    preview: str = ""
    file: Optional[str] = None
    line: Optional[int] = None

    def where(self) -> str:
        if self.file and self.line:
            return f" ({self.file}:{self.line})"
        if self.file:
            return f" ({self.file})"
        return ""


@dataclass
class Trial:
    """Everything one run of a spec against one URL produced."""

    host: str = ""
    url: str = ""
    ok: bool = False
    findings: List[Finding] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: Dict[str, int] = field(default_factory=dict)
    truncated: List[str] = field(default_factory=list)
    chapters: int = 0
    volumes: int = 0
    sampled: List[Dict[str, Any]] = field(default_factory=list)
    #: The values a fixture records and compares. Kept here so recording and replaying read
    #: the same structure rather than each reconstructing it.
    summary: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def add(self, **kwargs: Any) -> None:
        self.findings.append(Finding(**kwargs))


def _preview(value: Any) -> str:
    if value is None:
        return ""
    text = " | ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW else text[: PREVIEW - 1] + "…"


def _count(value: Any) -> Optional[int]:
    return len(value) if isinstance(value, list) else None


def run_trial(
    path: Path,
    url: str,
    fetcher: Fetcher,
    root: Optional[Path] = None,
    sample: int = 3,
) -> Trial:
    """Read the spec at *path*, crawl *url* with it, and report field by field."""
    path = Path(path)
    root = Path(root) if root else path.parent.parent
    text = path.read_text(encoding="utf-8")
    lines = line_map(text)
    name = path.name

    trial = Trial(host=path.stem, url=url)

    def note(
        field_name: str,
        ok: bool,
        detail: str = "",
        value: Any = None,
        required: bool = True,
    ) -> None:
        trial.add(
            field=field_name,
            ok=ok,
            required=required,
            detail=detail,
            count=_count(value),
            preview=_preview(value),
            file=name,
            line=locate(lines, field_name),
        )

    try:
        document = parse_yaml(text)
        spec = SourceSpec.model_validate(resolve_document(document, root=root, origin=path))
    except Exception as error:
        trial.error = str(error)
        return trial

    report = Report()
    interpreter = Interpreter.load(spec, fetcher, root=root, report=report)

    try:
        novel = interpreter.read_novel(url)
    except CrawlError as error:
        note(error.field, False, str(error).split(": ", 1)[-1])
        trial.error = str(error)
        _carry(trial, report)
        return trial
    except Exception as error:
        trial.error = f"{type(error).__name__}: {error}"
        _carry(trial, report)
        return trial

    _note_novel(note, novel)
    trial.chapters = len(novel.chapters)
    trial.volumes = len(novel.volumes)
    note(
        "toc.items",
        bool(novel.chapters),
        f"{len(novel.chapters)} chapters",
        novel.chapters and [c.title for c in novel.chapters[:3]],
    )

    for chapter in _sample(novel, sample):
        trial.sampled.append(_try_chapter(interpreter, novel, chapter, note))

    _carry(trial, report)
    trial.summary = summarise(novel, trial.sampled)
    trial.ok = trial.error is None and all(f.ok for f in trial.findings if f.required)
    return trial


#: What a fixture stores and compares.
COMPARED = ("title", "cover_url", "authors", "tags", "synopsis", "language")


def summarise(novel: Novel, sampled: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The result in the form a fixture records.

    Chapter *count* rather than the chapters, and body *lengths* rather than bodies: a fixture
    exists to notice a spec regression, and storing every body would make the file enormous
    while making a one-word site change fail the suite.
    """
    summary: Dict[str, Any] = {name: getattr(novel, name) for name in COMPARED}
    summary["chapters"] = len(novel.chapters)
    summary["volumes"] = len(novel.volumes)
    summary["first_chapter"] = novel.chapters[0].title if novel.chapters else ""
    summary["last_chapter"] = novel.chapters[-1].title if novel.chapters else ""
    summary["bodies"] = [{"id": s.get("id"), "characters": s.get("characters", 0)} for s in sampled]
    return summary


def _note_novel(note: Any, novel: Novel) -> None:
    note("novel.title", bool(novel.title), "", novel.title)
    for name, value in (
        ("cover", novel.cover_url),
        ("authors", novel.authors),
        ("tags", novel.tags),
        ("synopsis", novel.synopsis),
    ):
        note(f"novel.{name}", bool(value), "", value, required=False)


def _sample(novel: Novel, count: int) -> List[Chapter]:
    """First, middle and last. First-and-last would let a broken join through."""
    chapters = novel.chapters
    if not chapters:
        return []
    if len(chapters) <= count:
        return list(chapters)
    if count <= 1:
        return [chapters[0]]
    if count == 2:
        return [chapters[0], chapters[-1]]
    return [chapters[0], chapters[len(chapters) // 2], chapters[-1]]


def _try_chapter(interpreter: Interpreter, novel: Novel, chapter: Chapter, note: Any) -> Dict:
    label = f"chapter {chapter.id}"
    try:
        filled = interpreter.download_chapter(novel, chapter)
    except CrawlError as error:
        note(error.field, False, f"{label}: {str(error).split(': ', 1)[-1]}")
        return {"id": chapter.id, "ok": False, "error": str(error)}
    except Exception as error:
        note("chapter.body", False, f"{label}: {type(error).__name__}: {error}")
        return {"id": chapter.id, "ok": False, "error": str(error)}

    body = filled.body or ""
    note("chapter.body", bool(body), f"{label}: {len(body)} characters", body)
    return {
        "id": chapter.id,
        "ok": bool(body),
        "title": filled.title,
        "url": filled.url,
        "characters": len(body),
        "preview": _preview(body),
    }


def _carry(trial: Trial, report: Report) -> None:
    trial.warnings = list(report.warnings)
    trial.skipped = dict(report.skipped)
    trial.truncated = list(report.truncated)


def format_trial(trial: Trial) -> str:
    """The human-readable form. A reviewer reads this first."""
    out: List[str] = [f"{trial.host}  {trial.url}", ""]

    for finding in trial.findings:
        mark = "ok  " if finding.ok else ("FAIL" if finding.required else "none")
        detail = f"  {finding.detail}" if finding.detail else ""
        out.append(f"  {mark}  {finding.field}{detail}{finding.where()}")
        if finding.preview:
            out.append(f"          {finding.preview}")

    out.append("")
    out.append(f"  {trial.chapters} chapters in {trial.volumes} volume(s)")

    for stage, count in sorted(trial.skipped.items()):
        if count:
            # A large number means the selector is wrong even though the crawl succeeded.
            out.append(f"  {count} row(s) skipped in {stage}: check the selector")

    for stage in trial.truncated:
        out.append(f"  {stage} was truncated by a limit")

    for warning in trial.warnings:
        out.append(f"  warning: {warning}")

    if trial.error:
        out.append("")
        out.append(f"  error: {trial.error}")

    out.append("")
    out.append("  PASSED" if trial.ok else "  FAILED")
    out.append("")
    out.append("  Read the chapter titles before believing the count, and read one body.")
    return "\n".join(out)
