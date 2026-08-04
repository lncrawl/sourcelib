# lncrawl-sourcelib

Interpreter for declarative light-novel source definitions.

A source definition describes how to read one website as **data** rather than as code: one
YAML document per host, validated against a published schema and interpreted at runtime.
[RFC-0001](https://github.com/lncrawl/sourcelib/blob/main/docs/0001-source-definition.md)
is the normative definition of the format, and it lives here: one grammar version covers the
model, the step registry and the hook points together, so an errata and the code honouring it
are a single change. Where this package and the RFC disagree, the RFC wins and the package has
a bug.

The definitions themselves live in [lncrawl/sources](https://github.com/lncrawl/sources), which
also carries the guides for writing one.

```bash
pip install lncrawl-sourcelib          # validate, resolve and transform
pip install lncrawl-sourcelib[fetch]   # ...and reach a site
```

Fetching is an extra. Validating a spec needs no HTTP stack, and the definitions repository's
CI and anyone only writing YAML should not have to install a TLS impersonation library.

## What it does

Offline, on a checkout of the definitions repository:

```bash
sourcelib check                          # resolve and validate every document
sourcelib check --fixtures               # replay recorded pages
sourcelib resolve specs/example.com.yaml # what a spec actually says once merged
sourcelib schema -o schema.json          # the JSON Schema editors read
```

Against a live site, which is what the `fetch` extra is for:

```bash
sourcelib explain <url>                              # a structural digest, for writing a spec
sourcelib try specs/example.com.yaml <novel-url>     # run one spec, field by field
sourcelib record specs/example.com.yaml <novel-url>  # save the pages as a fixture
```

`try` reports each field and exits non-zero on a failure, so an agent loop needs no output
parsing. `--json` emits the same thing structurally.

```python
from sourcelib.spec.checks import check_resolved
from sourcelib.spec.resolve import resolve_file

spec = resolve_file("specs/example.com.yaml", root=".")
for problem in check_resolved(spec):
    print(problem)
```

`resolve` matters more than it looks. A spec can inherit through a chain of bases, so
"what am I actually running" has to be answerable in one command or a deep chain becomes
undebuggable.

## Development

```bash
uv sync
uv run poe test
uv run poe lint          # ruff, then pyright
uv run poe lint-fix
```

`[tool.poe.tasks]` in `pyproject.toml` is the full list.

The CLI runs from the checkout the same way, with the extra when a command reaches a site:

```bash
uv run sourcelib check <path-to-sources-checkout>/specs --strict
uv run --extra fetch sourcelib try <path>/specs/example.com.yaml <novel-url>
```

To work on a spec and the interpreter together, run the CLI from the definitions checkout
against this one, so an edit here takes effect without reinstalling:

```bash
uv run --with-editable <path-to-this-checkout> --with lncrawl-scraper \
  sourcelib try specs/example.com.yaml <novel-url>
```

The crawler resolves this package as an ordinary dependency. To point it at a checkout instead,
install it into the crawler's environment and then use `uv run --no-sync` there, because a
plain `uv run` re-syncs from the lock file and drops the override:

```bash
uv pip install -e <path-to-this-checkout>
uv run --no-sync python -m lncrawl ...
```

## Why it is its own package

It is not a feature of the crawler; it is the crawler's next core. Built inside the crawler
it would grow references into the code it replaces, and swapping it in would become a
rewrite instead of a dependency bump.

**This package never imports the crawler**, and its CI asserts that. It also means the
definitions repository can validate itself with no application in the loop.

## Licence

Apache-2.0. The crawler is GPL-3.0-or-later, and Apache-2.0 feeds a GPL-3 application
without friction. Nothing here is copied or adapted from the crawler's sources.
