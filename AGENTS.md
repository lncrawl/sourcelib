# AGENTS.md

Guidance for agents working in this repo: **`lncrawl-sourcelib`**, the interpreter for declarative
source definitions. It reads one YAML document per website and produces a novel, a chapter list and
chapter bodies. It is not a feature of a crawler; it is
[Lightnovel Crawler](https://github.com/lncrawl/lightnovel-crawler)'s next core, which is why it
lives on its own.

[docs/0001-source-definition.md](docs/0001-source-definition.md) is the **normative** format. This
package implements it, so where the two disagree the RFC is right and the code has a bug. A grammar
change is an RFC revision *before* it is a commit.

The definitions this interprets live in [lncrawl/sources](https://github.com/lncrawl/sources), which
also carries the author-facing guides. This file is about the interpreter.

## Skills

Deep, task-scoped knowledge lives in `.claude/skills/`. **Read the matching skill before starting
work in its area.**

| Skill                 | Use when                                                                |
| --------------------- | ----------------------------------------------------------------------- |
| `extend-the-format`   | Adding a step, a hook point, a placeholder or a model field              |
| `releasing`           | Cutting a release, changing CI, or shipping a fix a definitions repo needs |

## The one hard rule

**Never `import lncrawl`.** CI asserts it ([tests/test_decoupling.py](tests/test_decoupling.py)), and
that single rule is what makes replacing the crawler's core a deletion rather than a rewrite. The
consequence is that the models, the crawler protocol and the transform registry all live here, and
the crawler keeps its own copies plus a thin adapter for as long as its legacy tier survives.

`scraper` ([lncrawl-scraper](https://pypi.org/project/lncrawl-scraper/)) is the only sibling
dependency, and it is an **extra**: validating, resolving and transforming need no HTTP stack, so
`pip install lncrawl-sourcelib` stays light and `[fetch]` adds one. Import it lazily, never at module
scope in a path a validation-only install reaches.

## Commands

Toolchain: [uv](https://docs.astral.sh/uv/). [pyproject.toml](pyproject.toml) is the source of truth
for the task list, the lint rules, the Python floor and the dependencies.

```bash
uv sync
uv run poe test
uv run poe lint          # ruff, then pyright
uv run poe lint-fix
uv run poe schema        # regenerate the published JSON Schema
```

The CLI runs from the checkout, with the extra for anything that reaches a site:

```bash
uv run --extra fetch sourcelib try <spec> <novel-url>
uv run --extra fetch sourcelib explain <url>
```

## Layout

| Path                   | What it holds                                                             |
| ---------------------- | ------------------------------------------------------------------------- |
| `spec/model.py`        | The pydantic model. Every field, default and cross-field rule.            |
| `spec/schema.py`       | JSON Schema generation from that model, stamped with `x-generator`.        |
| `spec/loader.py`       | YAML reading, as 1.2 rather than PyYAML's 1.1.                            |
| `spec/resolve.py`      | `extends` merging, cycle and depth limits.                                |
| `spec/checks.py`       | Requirements a *resolved* spec must meet.                                 |
| `spec/extract.py`      | The extractor engine and the evaluation order.                            |
| `spec/items.py`        | Row reading, volume assignment, sorting.                                  |
| `transform.py`         | The typed step registry. The lowest layer: it imports nothing of ours.    |
| `interpolate.py`       | Template rendering. Closed placeholder and filter sets.                   |
| `fetch.py`             | Requests, `from` alternatives, pagination.                                |
| `runtime.py`           | The interpreter. Where a spec becomes a novel.                            |
| `hooks.py`             | Hook loading, the point set, the context passed to a hook.                |
| `trial.py`, `explain.py`, `fixtures.py` | `try`, `explain`, `record` and offline replay.            |

## Invariants that break silently

Each of these has already been got wrong once, and none of them failed loudly.

- **Hook points are derived from the stage set, and the calls honouring them are hand-written.**
  So the enum grows on its own while the code that consults it does not. Five points were once legal,
  bindable and dead, which a spec author cannot debug from outside. The test is parametrised over
  `hook_points()` so a point added later is covered without anyone remembering.
- **A filter yields nothing; a cleanup yields its input unchanged.** Get that backwards and a step
  deletes rows while the crawl still reports success. `FILTERS` in `transform.py` is the list.

  The corollary bites harder: **a cleanup with nothing *safe* to do must also change nothing.**
  `drop_leading` removed the first block matching a heading pattern, and on a theme wrapping the whole
  chapter in one element opening with its title that block was the body. A 5435-character chapter came
  out as a 172-character notice with every field reporting `ok`. Any step that removes something needs
  to ask whether what it found is the thing it was looking for.
- **Parallelism is a default, and the rate limit is the constraint.** Pages fetch concurrently
  wherever the termination condition allows, so a worker pool smaller than the declared pace paces
  *slower than the spec asked for*. `while` walks speculatively and must discard anything past the
  first empty page, however tempting the rows on it look, or the chapter list gains a gap.
- **Evaluation order is normative, not incidental.** `all` resolves before `pipe` so a pipe maps
  element-wise over a list. Swapping them changes what an existing spec means.
- **A parser wraps fragments and the depth is its choice.** `lxml` adds `<html><body>` and
  `html.parser` adds nothing, so any step reading a node's children must go through `_content_root`
  or it treats scaffolding as content. This produced `<p><html><body>text</body></html></p>` in a
  real recording, and separately made `drop_leading` find no blocks at all.
- **An unrecognised step or hook name is a load-time error, never a runtime one.** A spec using
  something this version does not know must fail before the crawl, not per chapter at 3am.
- **Concurrent pages are assembled by page index, never by completion order.** Chapter numbering is
  what a consumer stores and compares, so ordering by completion renumbers a library on every run.
- **YAML is read as 1.2.** Under 1.1 `on: url` parses as a boolean key and a var silently loses its
  scope, so a document would read correctly in a JavaScript parser and wrongly here.
- **IDNA2008, not the standard library's codec.** That one implements IDNA2003 and maps
  `faß.example` to `fass.example`, which is a different host rather than a different spelling.
- **`spec: 1` versions the model, the step registry and the hook points together.** Adding a step is
  a version bump exactly like adding a field.

## Anything that changes what a spec produces

The definitions repository records fixtures of real pages and replays them in CI. A change to
extraction, transformation or parsing therefore **invalidates every recording made before it**, and
that repository pins the interpreter version by `x-generator` inside its committed schema.

So say it in the changelog entry, plainly, and expect the sequence to be: release here, then bump
the pin and re-record there. There is no way to have one fixture that satisfies both versions.

## Conventions

- **ruff** and **pyright**, configured in [pyproject.toml](pyproject.toml), which owns line length
  and target version. Markdown is excluded from the formatter: `docs/` holds the RFC, whose Python
  blocks are illustrative and column-aligned on purpose.
- **Support the lowest Python the package can.** Never raise `requires-python` to satisfy a
  dependency or quiet a type-checker. Users run old interpreters.
- **Field documentation goes in `Field(description=...)`**, not in a comment. Those descriptions
  become the JSON Schema's, which is what drives editor hover text and what a model reads. A `#`
  comment is invisible to all of them.
- **Comments earn their place.** Default to zero. A comment is justified by a *why* the code cannot
  show: the alternative that looks right and is not, the behaviour that forced an odd choice.
- **`CHANGELOG.md`: one line per paragraph, kept short.** Never hard-wrap, because the release
  workflow lifts a section out verbatim and the renderer turns a single newline into a line break.
  An entry is a bold lead sentence plus the shortest *why* that would stop someone undoing it.
- **`README.md` is the PyPI long description**, so every link in it must be absolute. Relative paths
  resolve on GitHub and 404 there.
- Prose reads like a person wrote it. Sparing em-dashes; prefer a full stop, a colon or a comma.

## Commits

- **Never commit or push automatically.** When work is done, pause and draft a commit message for
  the user. Only run `git commit` when asked in that moment; prior approval does not carry over.
- **No AI attribution trailers.**
- Imperative subject, no type prefix. Body bullets for anything non-trivial. The reasoning belongs in
  the reply or the code, not in git history.
