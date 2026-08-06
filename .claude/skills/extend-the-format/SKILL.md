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
evidence accumulates instead of being re-derived.

Counting the *shapes* separately rather than the symptom is what makes the rule work. "Offset
pagination" looked like one gap across 26 hosts. Separating them showed 18 were merely numbered from
zero, which `paginate.first` covers; 7 paged by an offset that advances by what arrived, which
`paginate.step` describes wherever the host serves the page size it was asked for; and 1 asked for a
start-and-end range over a known total, which is a third mechanism and stayed a hook at one host. One
vague count would have argued for one vague feature.

### A count taken from the wrong place

`ItemList.script` was added on 2026-08-06 to read rows out of the JSON an element carries, and the
gap it fills is real: an `Extractor` reading `css: script#__NEXT_DATA__` with a `json:` path resolves,
while the same pair on a list means parse-then-select, so a list had no way to say it.

The **evidence offered for it was not**. Nine hosts were counted by grepping their crawlers for
`json.loads`, and that measures "this crawler parses JSON somewhere", not "this page carries its rows
in a script tag". Checked afterwards against the pages themselves: `teanovel` holds only its latest
chapter in `__NEXT_DATA__` and fetches the list from an API; `renovels`, `novelarrow`, `wuxia.click`
and `ranobe-novels` carry no chapter array in any script at all. The real count for this shape is
somewhere near zero, and what those hosts actually want is a way to describe an **API endpoint**,
which is a request rather than a container.

The lesson is the rule's own: count the shape, not the symptom, and count it where the shape lives —
on the site. A grep over the crawlers measures how someone once solved a problem, which is a
different fact from what the site serves.

Three rules hold the line and none is negotiable for convenience:

- **No control flow.** No conditionals, no user loops, no expressions, no arithmetic. `paginate` is the
  only iteration and it is declared. The moment a source needs an `if`, it uses a hook.
- **Steps auto-map over lists**, which is why there is no `map`. Element-wise application is a shape
  rule, not control flow.
- **Track the hook rate.** Past roughly 15% of specs carrying a hook, extend the grammar rather than
  work around it one source at a time.

## A grammar version covers three things at once

`spec: 1` versions **the model, the step registry and the hook point set together**.

**What forces a bump is meaning, not addition.** Change what an existing key, step or point *does* and
`spec` moves: an old interpreter recognises every name and quietly does the wrong thing. Add a new key
or step and it does not, on one condition that is load-bearing and implemented: an unrecognised key,
step or hook name is a **load-time** error naming it. `extra="forbid"` gives that for keys and
`_check_pipes` gives it for steps.

Do not "improve" this by requiring a bump for additions. It was the original rule and it was wrong in
practice: a spec naming the new key would declare a version an old client skips wholesale, and a *base*
doing so takes every spec extending it out of service at the same time. Loud refusal at load is the
better trade, and it is the failure the rule was written to prevent in the first place.

A later grammar arrives as a new RFC document, not as edits to this one. `spec:` exists so both can be
live at once.

## Adding a transform step

In `transform.py`, the lowest layer: it imports nothing of ours, so a step cannot reach the model.

- **Declare what it consumes and produces.** The registry entry is what lets a pipe whose types do not
  connect, or one naming a step that does not exist, be rejected at load time rather than mid-crawl.
  `_check_pipes` in `spec/checks.py` is what calls the registry; `validate_pipe` sat exported, tested
  and uncalled for four releases, so a misspelled step passed validation and raised per chapter.
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

`check_response` and `login` are the two points that belong to the session rather than to a stage, named
by `SESSION_HOOK_POINTS`. They are wired by `SessionFetcher` wrapping the fetcher, not by widening the
`Fetcher` protocol, so an implementation of that protocol does not grow a parameter it never uses. The
reachability test covers all seventeen points now, session ones included.

## After the change

The JSON Schema is generated from the model and stamped with `x-generator`, and the definitions
repository pins that version. So **anything that changes what a spec produces invalidates every
recorded fixture there.** Say it plainly in the changelog entry; the sequence is release here, then
bump the pin and re-record there.

Run `uv run poe lint` and `uv run poe test`. `poe schema` shows the diff a consumer will get.
