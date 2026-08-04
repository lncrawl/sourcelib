"""Command line entry point.

Only the offline commands exist so far: validating documents against the model, and
emitting the JSON Schema. `try`, `explain` and `record` need the interpreter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from sourcelib.spec.loader import load_file
from sourcelib.spec.schema import render, write


def _validate(paths: Sequence[Path], strict: bool) -> int:
    files: List[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.yaml")))
        else:
            files.append(path)

    if not files:
        print("nothing to validate", file=sys.stderr)
        return 0 if not strict else 1

    failed = 0
    for file in files:
        try:
            load_file(file)
        except Exception as error:
            failed += 1
            print(f"{file}: {error}", file=sys.stderr)

    ok = len(files) - failed
    print(f"{ok} of {len(files)} documents valid")
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="sourcelib")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate documents against the model")
    check.add_argument("paths", nargs="*", type=Path, default=[Path("specs"), Path("base")])
    check.add_argument("--strict", action="store_true", help="fail when nothing was found")

    emit = sub.add_parser("schema", help="print or write the JSON Schema")
    emit.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    emit.add_argument(
        "--check",
        action="store_true",
        help="fail if the file at --output differs from the generated schema",
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        return _validate(args.paths, args.strict)

    if args.command == "schema":
        if args.output is None:
            print(render(), end="")
            return 0
        changed = write(args.output) if not args.check else _schema_differs(args.output)
        if args.check and changed:
            print(f"{args.output} is out of date; run `sourcelib schema -o`", file=sys.stderr)
            return 1
        return 0

    return 1


def _schema_differs(path: Path) -> bool:
    return not path.exists() or path.read_text(encoding="utf-8") != render()


if __name__ == "__main__":
    raise SystemExit(main())
