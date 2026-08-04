# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-08-05

### Changed

- `paginate.count` is renamed `last`, and `paginate.limit` is gone.

### Added

- **A `min` transform step**, the counterpart to `max`: the lowest number in a list, ignoring entries that carry none. It reads which page a site calls its first, off the same pager `max` reads the last from.

- **`paginate.first`**, the number a site gives its first page, so a spec can say when that is not 1.

- Pages are fetched in parallel by default, wherever the termination condition allows. Set `paginate.concurrent` to `false` to disable concurrency, on by default where range is known. `true` with `next` is refused because those addresses are only known in turn.

- **`try --toc-pages N`** caps the chapter-list walk, which is where a trial spends its time. The reported chapter count is then short and says so.

### Fixed

- **`rate_limit` was ignored.** No command passed it to the fetcher, so every run went at full speed however politely a spec asked. Read from the resolved document, because a spec inherits the pace its base set for a reason.

- **`--sample` capped at three** however many were asked for. Samples now spread evenly across the list, always including the first and the last. The default of three is unchanged, so existing recordings still replay. Spread rather than random, because a fixture records the chapters it sampled.

- **`drop_leading` could delete a whole chapter and still report `ok`.** Some themes wrap the body in one element that opens by repeating the title, and that block matched. It now removes only a block shaped like a heading: no blocks of its own, and not almost all of the text. Section 6.2 already required a cleanup with nothing to do to change nothing.

## [0.1.3] - 2026-08-04

### Changed

- **`lxml` is the default parser**, and a dependency. It recovers from real-world markup better, which matters because a selector matching nothing silently is the usual defect. `parser:` still overrides it for the pages lxml restructures.

  **This changes what a spec produces**, so a fixture recorded before this release will not replay. Re-record after upgrading.

### Fixed

- **Five hook points were legal, bindable and never called**: `search.items`, `novel.language`, `toc.volumes`, `chapter.request` and `chapter.url`. Points are derived from the stage set while the calls honouring them are hand-written, so the test is now parametrised over `hook_points()` and a point added later is covered without anyone remembering.

- **A parsed fragment carried the parser's wrapper into its output.** `lxml` wraps a fragment in `<html><body>` and `html.parser` adds nothing, so steps reading a node's children treated that as content: a synopsis arriving as a JSON string came out as `<p><html><body>text</body></html></p>`. The same off-by-one-level meant `drop_leading` found no blocks at all.

- **`paragraphs` leaked a BeautifulSoup warning to stderr** when a fragment looked like a URL, which on a Blogger post is an ordinary paragraph.

## [0.1.2] - 2026-08-04

All four come from running the shared WordPress base against a live Madara host, and every one made `from` less useful than the RFC describes.

### Fixed

- **`from` gave up on the first alternative that failed unexpectedly.** Only a `FetchError` was caught, but the HTTP layer raises its own for a `404`, and an endpoint absent from an installation is the case the fallback list exists for. Any failure now falls through, and the error names what each alternative did.

- **`from` never checked whether an alternative produced items**, so the first that merely fetched won. An ajax endpoint answering `200` with an empty body reported zero chapters rather than falling through to the page holding them.

- **A URL template's doubled slash reached the site.** `{novel_url}/ajax/chapters/` is the natural spelling, and a trailing slash made it `//ajax/chapters/`, which enough sites answer with a `404`. The path is collapsed after rendering; a query string keeps its slashes, since a `//` there can be data.

- **A `page` naming a request that had not run failed mid-crawl** rather than at load time, as section 3.6 requires. `novel: request: {page: novel}` reads as "the novel page" and is the natural way to write the mistake. Self-references, forward references and `from` alternatives are all checked now.

## [0.1.1] - 2026-08-04

Both fixes come from the first run against a live site.

### Fixed

- **A chapter body was unreadable whenever its pipe began with one of six steps.** The rule keeping a selected element intact for a node-consuming pipe (section 3.4) consulted a hand-written list holding five of eleven steps, so a pipe starting with `drop_leading`, `keep_attrs`, `drop_empty_nodes`, `unwrap_all`, `inner_html` or `text` failed with `expected a node, got str`. The set is read from the registry now, which already declares what every step consumes.

- **`explain` answered a failed retrieval with a traceback**, in the one command a contributor runs first. It reports the reason and exits non-zero, as `try` already did.

## [0.1.0] - 2026-08-04

First release. Implements [RFC-0001](docs/0001-source-definition.md) at `spec: 1`.

### Added

- **The source definition model**, with the JSON Schema generated from it rather than maintained beside it. The schema records its generator in `x-generator`, so the definitions repository regenerates with that exact version instead of whatever is latest.

- **`extends` resolution.** Scalars replace, mappings merge, `fallback` lists prepend so a child's selector is tried first while the parent's still follow, and every other list replaces because step order in a pipe is semantic. Cycles, over-deep chains and an `extends` into `disabled/` are refused.

- **The transform registry**, 28 typed steps, with a pipe whose types do not connect rejected at validation time rather than mid-crawl. A scalar step applied to a list runs element-wise, which is why the format needs no `map`.

- **The extractor engine.** `css`, `json`, `regex`, `header` and `const`, in the order the RFC fixes. URL-valued fields resolve against the document they came from rather than `base_url`, so a stage that paginated into a subdirectory still produces correct links.

- **Requests and pagination.** All three termination conditions, `from` alternatives, form harvesting in both readings, and body-shape inference for a payload. Concurrent pages assemble by page index, never by completion order, because chapter numbering is what a consumer stores.

- **The interpreter**, turning a resolved spec into a novel, a table of contents with volumes, and bodies joined across pages. A missing title or an empty chapter list is an error naming the field; a missing cover, author, tag list or synopsis is a warning, because real pages omit those.

- **Hooks**, the only escape hatch. Bound by point name, loaded by path, and refused if they import another host's implementation, checked from the syntax tree before the module executes. The context is a parameter and never ambient: a hook module is shared by every crawl, so only an argument can say which it serves.

- **`sourcelib try`**, reporting what every field produced and naming the spec field, its file and its line when one fails. Structured first and formatted second, with the exit status as the verdict, so an agent loop needs no output parsing.

- **`sourcelib explain`**, a structural digest of a page at roughly 3% of its size. Classes that look build-generated are never offered as selectors, because a bundler hash breaks on the next deploy.

- **`sourcelib record`** and offline replay, so CI can notice a change breaking a spec. Body lengths rather than bodies: a fixture catches a spec regression and should not fail on a one-word site edit.

### Notes

- **YAML is read as 1.2, not PyYAML's 1.1.** Under 1.1 `on: url` parses as a boolean key and a var silently loses its scope, so a document would read correctly in a JavaScript parser and wrongly here.

- **IDNA2008 is required, not the standard library's codec.** That one implements IDNA2003 and maps `faß.example` to `fass.example`, a different host rather than a different spelling.

- **Fetching is an extra.** A base install validates, resolves and transforms with no HTTP stack; `[fetch]` adds one. The definitions repository's CI and anyone only writing YAML should not need a TLS impersonation library.

[0.1.4]: https://github.com/lncrawl/sourcelib/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lncrawl/sourcelib/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lncrawl/sourcelib/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/lncrawl/sourcelib/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lncrawl/sourcelib/releases/tag/v0.1.0
