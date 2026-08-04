---
name: extend-the-format
description: Add or change a transform step, a hook point, a placeholder or a model field — the RFC-first order, what a grammar version covers, the reachability trap for derived points, and the five-sources rule. Use when the format itself needs to change rather than a spec.
---

# Changing the format

The format is a contract, so the order is fixed: **the RFC changes first**, then the model, then the
implementation, then the tests. Reversing it produces a document that describes something else, which
is worse than no document because people trust it.

[docs/0001-source-definition.md](../../../docs/0001-source-definition.md) is normative. If the code
and the RFC disagree, the code has a bug.

## Does it earn its place

**Any new key needs five or more real sources that want it.** That rule applies to your own ideas too;
it is what has kept the format from becoming a bad programming language. Applied honestly during
design it removed `requires`, a login stage, `of`/`join`, a separate `attrs` field and a rejected-hosts
index.

Until something clears that bar, the answer is a hook. Record the count when you decline, so the
evidence accumulates instead of being re-derived. Two gaps are open on exactly this basis: row
filtering by a sibling field, and paging by item offset rather than page number.

The second one shows how the counting pays off. It started as "offset pagination" across 26 hosts,
which sounded like one gap; separating the shapes showed that 18 of those were simply numbered from
zero, which `paginate.first` now covers, leaving 8 that genuinely page by item offset. A vague count
would have argued for a vague feature.

Three rules hold the line and none is negotiable for convenience:

- **No control flow.** No conditionals, no user loops, no expressions, no arithmetic. `paginate` is the
  only iteration and it is declared. The moment a source needs an `if`, it uses a hook.
- **Steps auto-map over lists**, which is why there is no `map`. Element-wise application is a shape
  rule, not control flow.
- **Track the hook rate.** Past roughly 15% of specs carrying a hook, extend the grammar rather than
  work around it one source at a time.

## A grammar version covers three things at once

`spec: 1` versions **the model, the step registry and the hook point set together**. Adding a step is a
version bump exactly like adding a field.

Miss that and the failure is nasty: a spec using a new step is still schema-valid, so an old
interpreter accepts the document and dies on an unknown step name mid-crawl, per chapter, with nothing
pointing at the cause. Two rules close it, and both are implemented: any registry or hook-point
addition bumps `spec`, and an unrecognised step or hook name is a **load-time** validation error
naming the step.

A later grammar arrives as a new RFC document, not as edits to this one. `spec:` exists so both can be
live at once.

## Adding a transform step

In `transform.py`, the lowest layer: it imports nothing of ours, so a step cannot reach the model.

- **Declare what it consumes and produces.** The registry entry is what lets a pipe whose types do not
  connect be rejected at validation time rather than mid-crawl.
- **Decide whether it is a filter or a cleanup, and get it right.** A filter yields nothing when it
  does not match; a cleanup yields its input unchanged. Backwards, a step deletes rows while the crawl
  reports success. `FILTERS` is the list and section 6.2 is the reason it exists.
- **If it reads a node's children, go through `_content_root`.** `lxml` wraps a fragment in
  `<html><body>` and `html.parser` adds nothing, so a step that walks children without skipping that
  treats scaffolding as content.
- Nothing in the registry may be copied or adapted from the crawler's `cleaner.py`. Different licence.

## Adding a hook point

Points are **derived** from the stage set in `spec/model.py`, so the enum grows on its own. The calls
that honour it do not.

That gap has already produced the worst failure shape in the project: five points were legal, bindable
and never called, so a spec author bound one, passed validation and got silence. The test in
`tests/test_hooked_crawl.py` is parametrised over `hook_points()` for this reason. **Adding a stage
field adds points, so run that test and confirm the new ones fire.**

Two points, `check_response` and `login`, are specified and deliberately not yet wired: they belong to
the session rather than to a crawl, and `check_response` needs the fetcher protocol to carry it.
`SESSION_HOOK_POINTS` names them and the reachability test excludes them.

## After the change

The JSON Schema is generated from the model and stamped with `x-generator`, and the definitions
repository pins that version. So **anything that changes what a spec produces invalidates every
recorded fixture there.** Say it plainly in the changelog entry; the sequence is release here, then
bump the pin and re-record there.

Run `uv run poe lint` and `uv run poe test`. `poe schema` shows the diff a consumer will get.
