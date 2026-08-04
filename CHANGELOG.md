# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-08-04

All four come from running the shared WordPress base against a live Madara host, and every one made `from` less useful than the RFC describes.

### Fixed

- **`from` gave up on the first alternative that failed in an unexpected way.** Only a `FetchError` was caught, but the HTTP layer raises its own exception for a `404`, and an endpoint absent from this installation is the exact case the fallback list exists for. Any failure now falls through, and the reason names each alternative's exception when all of them fail.

- **`from` never checked whether an alternative produced items.** The predicate was passed for pagination but not for the fallback list, so the first alternative that merely fetched won. An ajax endpoint answering `200` with an empty body therefore reported zero chapters instead of falling through to the page holding them, which made a fallback list decorative wherever a dead endpoint stays reachable.

- **A URL template's doubled slash reached the site.** `{novel_url}/ajax/chapters/` is the natural way to write that request, and a novel URL ending in `/` made it `.../a-title//ajax/chapters/`. Enough sites answer the doubled form with a `404` that leaving it to the author means every such template carries the same latent bug. The path is now collapsed after rendering; a query string keeps its slashes, since a `//` there can be data.

- **A `page` naming a request that had not run was a mid-crawl failure.** RFC-0001 section 3.6 requires rejecting it, but the only check was the fetcher's own cache lookup, which reported a missing page rather than a spec defect. `novel: request: {page: novel}` reads as "the novel page" and is the natural way to write this mistake. Self-references, forward references and `from` alternatives are all checked when the spec loads.

## [0.1.1] - 2026-08-04

Both fixes come from the first run against a live site.

### Fixed

- **A chapter body was unreadable whenever its pipe began with one of six steps.** The rule that keeps a selected element intact for a pipe that consumes a node (RFC-0001 section 3.4) consulted a hand-written list of node-consuming steps holding five of the eleven. A pipe starting with any of `drop_leading`, `keep_attrs`, `drop_empty_nodes`, `unwrap_all`, `inner_html` or `text` therefore had its element flattened to a string first, and the step failed with `expected a node, got str`. Since `drop_leading` is how a duplicated chapter heading is removed, this hit ordinary bodies rather than unusual ones. The set is now read from the transform registry, which already declares what every step consumes, so a second list cannot fall behind it.

- **`explain` answered a failed retrieval with a traceback.** A mistyped URL or a `404` printed a stack trace instead of a reason, in the one command a contributor runs first. It now reports the failure on stderr and exits non-zero, as `try` already did.

## [0.1.0] - 2026-08-04

First release. Implements [RFC-0001](https://github.com/lncrawl/sources/blob/main/docs/0001-source-definition.md) at `spec: 1`.

### Added

- **The source definition model**, with the JSON Schema generated from it rather than maintained beside it. The schema records the version that produced it in `x-generator`, so the repository holding the specs regenerates with that exact version instead of whatever is latest.

- **`extends` resolution.** Scalars replace, mappings merge, `fallback` lists prepend so a child's selector is tried first while everything the parent knew still follows, and every other list replaces because step order in a pipe is semantic. Cycles and chains past a depth limit are refused, as is an `extends` pointing into `disabled/`.

- **The transform registry**, 28 typed steps, with a pipe whose types do not connect rejected at validation time rather than mid-crawl. A scalar step applied to a list runs element-wise, which is why the format needs no `map`.

- **The extractor engine.** `css`, `json`, `regex`, `header` and `const`, evaluated in the order the RFC fixes: source, `all`, `attr`, `pipe`, `default`, then `fallback`. URL-valued fields resolve against the document they came from rather than against `base_url`, so a stage that paginated into a subdirectory still produces correct links.

- **Requests and pagination.** All three termination conditions, `from` alternatives, form harvesting in both of its readings, and body-shape inference for a payload. Concurrent pages are assembled by page index rather than by completion order, because chapter numbering is what a consumer stores and compares.

- **The interpreter**, turning a resolved spec into a novel, a table of contents with volumes, and chapter bodies joined across pages. A missing title or an empty chapter list is an error naming the field; a missing cover, author, tag list or synopsis is a warning, because real pages omit those often enough that failing would reject working sources.

- **Hooks**, the only escape hatch. Bound by point name, loaded by path, and refused if they import from another host's implementation — checked from the syntax tree before the module executes, so a forbidden import cannot already have run. The context is passed as a parameter and never looked up ambiently: a hook module is shared by every crawl, so only an argument can say which one it is serving.

- **`sourcelib try`**, which reports what every field produced and names the spec field, its file and its line when one fails. Structured output first and formatted second, and the exit status is the verdict, so an agent loop needs no output parsing. Chapters are sampled first, middle and last, since first-and-last alone would let a broken `join` through on a body split across pages.

- **`sourcelib explain`**, a structural digest of a page at roughly 3% of its size: what repeats and how many rows, which script carries the data, and where a page count would come from. Classes that look build-generated are never offered as selectors, because a bundler hash breaks on the next deploy.

- **`sourcelib record`** and offline replay, so CI can notice a change breaking a spec. Body lengths rather than bodies, since a fixture exists to catch a spec regression and not to fail on a one-word site edit.

### Notes

- **YAML is read as 1.2, not PyYAML's 1.1.** Under 1.1 `on: url` parses as a boolean key and a var silently loses its scope, so a document would read correctly in a JavaScript or Go parser and wrongly here.

- **IDNA2008 is required, not the standard library's codec.** That one implements IDNA2003, which maps `faß.example` to `fass.example` — a different host rather than a different spelling of one.

- **Fetching is an extra.** `pip install lncrawl-sourcelib` validates, resolves and transforms with no HTTP stack; `[fetch]` adds one. The definitions repository's CI and anyone only writing YAML should not need a TLS impersonation library.

[0.1.2]: https://github.com/lncrawl/sourcelib/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/lncrawl/sourcelib/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lncrawl/sourcelib/releases/tag/v0.1.0
