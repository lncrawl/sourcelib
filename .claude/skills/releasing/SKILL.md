---
name: releasing
description: Cut a release — the bump/release/publish chain, what a version bump obliges downstream, the changelog format the workflow lifts verbatim, and the fixture consequence in the definitions repo. Use when releasing, changing CI workflows, or when a definitions repo needs a fix from here.
---

# Releasing

Three workflows, run in order from the Actions tab. Read them rather than trusting this list:
`.github/workflows/{bump,release,publish}.yml`.

| Workflow         | Does                                                                    |
| ---------------- | ----------------------------------------------------------------------- |
| **Bump Version** | Writes the new version, commits, pushes the `v*` tag, then triggers Release |
| **Release**      | Cuts the GitHub release from the tag, with the changelog section as notes |
| **Publish**      | Builds and uploads to PyPI by trusted publishing, no stored token        |

`CI` runs on push and holds lint, tests and the assertion that nothing imports the crawler.

## Before bumping

- `uv run poe lint` and `uv run poe test` clean.
- The changelog section for the new version is written.
- **Decide whether this release changes what a spec produces.** That is the question that matters
  most, and it is answered in the next section.

## The changelog is lifted verbatim

The release workflow copies a version's section into the release notes, and the renderer on the other
side turns a single newline into a line break.

- **Never hard-wrap.** One line per paragraph and per bullet, or the note arrives as a ragged column
  with its indentation showing.
- An entry is a bold lead sentence plus the shortest *why* that would stop someone undoing it. Not the
  investigation that produced it.
- Keep the reference-style link definitions at the bottom in step, including a `compare` link for the
  new version.

## A release that changes output has a downstream cost

The definitions repository ([lncrawl/sources](https://github.com/lncrawl/sources)) records fixtures of
real pages and replays them in CI, and it pins **this** version by `x-generator` inside its committed
schema. So any change to extraction, transformation or parsing **invalidates every recording made
before it**, and there is no way to have one fixture that satisfies both versions.

Say so in the changelog entry. Then the sequence there is:

1. Release here.
2. In the definitions repo: `uv lock --upgrade-package lncrawl-sourcelib`, then `uv sync`. Its lockfile
   pins the old version, so a plain `uv sync` will keep reinstalling it and the mismatch looks like a
   broken fixture.
3. `sourcelib schema -o schema/source.v1.json` to move the pin.
4. Re-record the affected fixtures and run `poe all`.

`poe pin` there reports a local mismatch, but note what it cannot catch: if the schema pin and the
installed version are both stale, they agree and the check passes while the fixtures disagree with
both. The lockfile is the thing to suspect.

## PyPI takes a moment

The JSON API is cached, so a freshly published version can 404 for a minute while the upload has
already succeeded. Check the publish log for `200 OK` before concluding anything failed, and use
`--no-cache` when installing to bypass uv's index cache.

## Versioning

`spec:` versions the format and is independent of the package version. A package release does not
change the grammar; a grammar change is an RFC revision and bumps `spec:`. Read the
`extend-the-format` skill before touching either.
