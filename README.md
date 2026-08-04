# lncrawl-sourcelib

Interpreter for declarative light-novel source definitions.

A source definition describes how to read one website as **data** rather than as code: one
YAML document per host, validated against a published schema and interpreted at runtime.
[RFC-0001](https://github.com/lncrawl/sources/blob/main/docs/0001-source-definition.md) in
the `lncrawl/sources` repository is the normative definition of the format. This package is
written against it, and where the two disagree the RFC wins.

```bash
pip install lncrawl-sourcelib
```

## What it does

```bash
sourcelib check                          # resolve and validate every document
sourcelib resolve specs/example.com.yaml # what a spec actually says once merged
sourcelib schema -o schema.json          # the JSON Schema editors read
```

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

## Why it is its own package

It is not a feature of the crawler; it is the crawler's next core. Built inside the crawler
it would grow references into the code it replaces, and swapping it in would become a
rewrite instead of a dependency bump.

**This package never imports the crawler**, and its CI asserts that. It also means the
definitions repository can validate itself with no application in the loop.

## Licence

Apache-2.0. The crawler is GPL-3.0-or-later, and Apache-2.0 feeds a GPL-3 application
without friction. Nothing here is copied or adapted from the crawler's sources.
