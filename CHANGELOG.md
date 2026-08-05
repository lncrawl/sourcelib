# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.6] - 2026-08-05

### Added

- `try-search`, running a spec's search stage against a live query. Takes a path and a query, no novel URL.
- A browser solver is configured when the machine has one, so `request.render` works and a challenged host can be reached. Needs `lncrawl-scraper[cdp]` and a Chromium-family build; absent, nothing changes.

### Fixed

- `json` with `css` on one item list parses, then selects. It selected first and found nothing.
- `const` is interpolated, as section 3.4 has always said, so a field can be produced from `vars` alone.
- Templates are validated at load time. An unknown placeholder, an unknown filter and a placeholder used outside its stage all passed `check` and were found by a crawl.

### Changed

- A var's `const` may not name another var, and is refused at load time. RFC-0001 section 3.5.

## [0.1.5] - 2026-08-05

### Added

- `paginate.step`, how much `{page}` advances between pages.
- `require` on an item list, naming further fields a row cannot do without.
- A `strip_matching` step, removing an element whose text matches a pattern.
- `check_response` and `login` are called.

### Fixed

- A pipe naming a step that does not exist passed validation.
- A `require` naming an undeclared field is refused at load time.
- The JSON Schema rejected the `null` that deletes an inherited key.
- A pipe fed by `all: true` was type-checked as though it took a scalar.

### Changed

- `spec` increments on a change in meaning, not on an addition.

## [0.1.4] - 2026-08-05

### Changed

- `paginate.count` is renamed `last`, and `paginate.limit` is gone.

### Added

- A `min` transform step, the counterpart to `max`.
- `paginate.first`, the number a site gives its first page.
- Pages are fetched in parallel wherever the termination condition allows. `paginate.concurrent: false` turns it off.
- `try --toc-pages N` caps the chapter-list walk.

### Fixed

- `rate_limit` was never passed to the fetcher.
- `--sample` capped at three. Samples now spread evenly across the list.
- `drop_leading` could delete a whole chapter and still report `ok`.

## [0.1.3] - 2026-08-04

### Changed

- `lxml` is the default parser, and a dependency. **A fixture recorded before this release will not replay.**

### Fixed

- Five hook points were legal, bindable and never called.
- A parsed fragment carried the parser's `<html><body>` wrapper into its output.
- `paragraphs` leaked a BeautifulSoup warning to stderr.

## [0.1.2] - 2026-08-04

### Fixed

- `from` gave up on the first alternative that failed unexpectedly.
- `from` never checked whether an alternative produced items.
- A URL template's doubled slash reached the site.
- A `page` naming a request that had not run failed mid-crawl rather than at load time.

## [0.1.1] - 2026-08-04

### Fixed

- A chapter body was unreadable whenever its pipe began with one of six steps.
- `explain` answered a failed retrieval with a traceback.

## [0.1.0] - 2026-08-04

First release. Implements [RFC-0001](docs/0001-source-definition.md) at `spec: 1`.

### Added

- The source definition model, with the JSON Schema generated from it and stamped with the version that produced it.
- `extends` resolution: scalars replace, mappings merge, `fallback` prepends, every other list replaces.
- The transform registry, typed, with a pipe whose types do not connect refused at validation time.
- The extractor engine: `css`, `json`, `regex`, `header` and `const`.
- Requests and pagination: three termination conditions, `from` alternatives, and form harvesting.
- The interpreter, producing a novel, a table of contents with volumes, and bodies joined across pages.
- Hooks, bound by point name and refused if they import another host's implementation.
- `sourcelib try`, reporting what every field produced and naming the spec field that failed.
- `sourcelib explain`, a structural digest of a page.
- `sourcelib record` and offline replay.

### Notes

- YAML is read as 1.2, not PyYAML's 1.1.
- IDNA2008 is required, not the standard library's codec.
- Fetching is an extra. A base install validates, resolves and transforms with no HTTP stack.

[0.1.6]: https://github.com/lncrawl/sourcelib/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/lncrawl/sourcelib/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/lncrawl/sourcelib/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lncrawl/sourcelib/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lncrawl/sourcelib/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/lncrawl/sourcelib/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lncrawl/sourcelib/releases/tag/v0.1.0
