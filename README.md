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
sourcelib check specs/            # validate documents against the model
sourcelib schema -o schema.json   # write the JSON Schema editors read
```

```python
from sourcelib import SourceSpec
from sourcelib.spec.loader import load_file

spec = load_file("specs/example.com.yaml")
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
