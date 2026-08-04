# RFC-0001: Source Definition format

| **Version** | `spec: 1`  |
| ----------- | ---------- |
| **Created** | 2026-08-03 |

## 1. Summary

A **source definition** describes how to read one website: where the novel title is, how to find
the chapter list, which element holds the chapter text. This RFC defines that description as
**data**. One document per host, validated against a published schema, and interpreted at runtime.

Data can be validated, diffed, form-edited and generated. It can be tested without being executed,
so authoring needs no sandbox and no privileged account. A machine-checkable schema plus a
structured test runner gives a repair loop that a person or a model can close without reading a
stack trace.

**This document is the contract** between the interpreter, the definitions repository's CI, the web
editor and the published JSON Schema. Where it and an implementation disagree, one of them is wrong;
they are never both right.

It lives beside the interpreter because `spec: 1` versions the model, the step registry and the hook
points as one thing, and all three are implemented here. An errata and the code that honours it are
then one change rather than two that can drift. The schema is generated from that implementation, so
it cannot contradict the model; this prose is what a schema cannot express.

`spec: 1` is frozen. A change to the model, the step registry or the hook points arrives as a new
version of this document, and both stay live because every spec declares the version it is written
for.

## 2. Terminology

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY and
OPTIONAL are to be interpreted as described in BCP 14 [RFC2119] [RFC8174] **when, and only when, they
appear in all capitals**. The same words in lower case carry their ordinary English meaning and impose
no requirement. This document uses both, so the distinction matters.

| Term              | Meaning                                                                      |
| ----------------- | ---------------------------------------------------------------------------- |
| **Host**          | A normalised hostname. See §8.1.                                             |
| **Spec**          | One source definition document.                                              |
| **Concrete spec** | A spec that declares `base_url`. It identifies a host and can be registered. |
| **Abstract spec** | A spec with no `base_url`. It exists to be extended and is never registered. |
| **Resolved spec** | The single document produced by merging a spec with its ancestors. See §5.   |
| **Extractor**     | A description of how to read one value out of a document. See §3.4.          |
| **Pipe**          | An ordered list of transform steps applied to an extracted value. See §6.    |
| **Hook**          | A named Python function a spec may call at one fixed point. See §7.          |
| **Interpreter**   | Software that reads a spec and crawls a site with it.                        |
| **Manifest**      | The published index clients poll to learn what changed. See §9.              |

A spec document is YAML [YAML] in a repository and JSON on the wire. Both MUST parse to the same
model, and an implementation MUST accept either.

## 3. The model

The normative definition is a set of typed structures. An implementation SHOULD express them as a
schema-emitting model so the JSON Schema in §3.10 is generated rather than maintained.

Field documentation MUST live in the model's own description metadata, not in code comments. Those
descriptions become the JSON Schema's `description`, which drives editor hover text, autocompletion,
and what a model reads. A comment reaches none of those.

### 3.1 Two layers

This document defines two things, and they are kept separate so that a second kind of source can
arrive without redesigning the first:

| Layer                       | Defines                                                                              | Sections                     |
| --------------------------- | ------------------------------------------------------------------------------------ | ---------------------------- |
| **Mechanism**               | Reading a value, making a request, paging, transforming, inheriting, escaping to code | §3.4 to §3.9, §4 to §7       |
| **The light-novel binding** | Which stages exist, what fields they carry, and what a source is a source *of*        | §3.2, §3.3, §3.8             |

The mechanism layer names no genre. An `Extractor` reads a value; it does not know a title from a
price. A `Request` fetches, `paginate` iterates, a pipe transforms. None of it is specific to books.

The binding layer is where this document commits to light novels: four stages, the field set `title`,
`cover`, `authors`, `tags`, `synopsis`, the volume rules, and `chapters_per_volume`.

**A future domain arrives as a new RFC that reuses the mechanism layer unchanged and defines its own
binding.** It cites §3.4 to §3.9 and §4 to §7 rather than restating them, and defines its own stages
and fields in place of §3.8. Nothing in the mechanism layer may be extended in a way that depends on a
stage name or a field name from the binding layer.

There is no domain selector in the model and no generic stage map. The separation is structural.

### 3.2 SourceSpec

```python
SourceSpec:
    spec:                int
    extends:             RepoPath | None = None
    base_url:            HttpUrl  | None = None
    language:            str      | None = None
    rate_limit:          float = 3.0
    chapters_per_volume: int   = 100
    has_manga:           bool  = False
    has_mtl:             bool  = False
    parser:              str | None = None
    encoding:            str | None = None
    headers:             dict[str, str] = {}
    can_search:          bool | None = None
    can_login:           bool | None = None
    disabled:            str  | None = None

    vars:    dict[str, Var]            = {}
    pipes:   dict[str, list[Step]]     = {}
    hooks:   dict[HookPoint, RepoPath] = {}

    search:  SearchStage  | None = None
    novel:   NovelStage   | None = None
    toc:     TocStage     | None = None
    chapter: ChapterStage | None = None
```

`spec` is REQUIRED. It names the version of **the whole contract**: this model, the step registry in
§6, and the hook points in §7. An interpreter implements every version up to the one it was written
for. It MUST refuse to load a document whose `spec` exceeds that, MUST NOT attempt partial
interpretation, and MUST NOT refuse a document whose `spec` is lower.

A change to the contract is either **breaking** or **additive**:

| Class        | What it does                                                     | `spec`         |
| ------------ | ---------------------------------------------------------------- | -------------- |
| **Breaking** | Changes what an existing key, step or point means, or removes one | MUST increment |
| **Additive** | Adds a key, step or point, leaving existing meanings intact       | MUST NOT change |

The additive class depends on an unrecognised name being refused rather than ignored: §3.9.1 for a key,
§6.1 for a step, §3.9.2 for a hook point, each at load time and each in an error naming what was not
recognised. An implementation that does not enforce all three MUST treat every change as breaking.

`base_url`, when present, MUST be an absolute `http` or `https` URL whose host matches the document's
filename (§8.2). Its absence makes the spec abstract.

`language` is OPTIONAL and is a **starting default only**. It is an ISO 639-1 two-letter code
[ISO639].

**Language is detected, not demanded.** What is in the document beats what a file declared:

| Precedence | Source                                                                                       |
| ---------- | -------------------------------------------------------------------------------------------- |
| weakest    | `"und"`, the ISO 639-2 code for undetermined                                                 |
|            | the spec's `language`                                                                        |
|            | the `novel.language` extractor, if the spec has one                                          |
| strongest  | detection from the fetched content: `<html lang>`, then `og:locale`, then content heuristics |

`parser` names the markup parser, for example `html.parser`. Some sites serve markup that a lenient
parser silently restructures, producing selectors that match nothing for no visible reason.

`encoding` names a character encoding, for example `gbk`, for sites that do not declare their own
correctly.

`headers` applies to every request the source makes. Per-request headers (§3.6) merge over it.

Implementations MUST NOT let a spec set a `User-Agent` or control header ordering. Those belong to the
HTTP layer, which uses them to present a consistent identity.

`disabled` carries a human-readable reason. It MUST be present if and only if the document lives in
`disabled/` (§8.3).

`can_search` and `can_login` are tri-state:

| Value            | Meaning                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ |
| `None` (default) | Derived. The source can search if the resolved spec has a `search` stage, and can log in if it has a `login` hook. |
| `false`          | Forced off, even though the capability resolves.                                                                   |
| `true`           | Invalid unless the capability actually resolves. Implementations MUST reject it otherwise.                         |

Deriving is the default because a hand-maintained capability flag drifts from the code that implements
it. The explicit `false` lets a child turn an inherited capability off without discarding the
configuration behind it, which is what a mirror with a broken search endpoint needs.

### 3.3 What a resolved spec must be able to do

All four stages are OPTIONAL in the model, because a spec need not declare what it inherits. A
two-line alias spec declares no stages at all, and a never-implemented host in `disabled/` declares
none ever.

The requirement applies to the **resolved** spec:

> A resolved spec that is concrete and not disabled MUST be able to produce a novel, a table of
> contents and a chapter body. Each MUST come from its stage or from a hook bound to that stage's
> point (§3.9.2), and `toc` MUST yield chapters via `items` or a `toc.items` hook.

Implementations MUST validate this after merging, and MUST NOT apply it to an unresolved document.
Validating the raw file would reject the two-line alias that `extends` exists to enable.

A hook satisfies the requirement its stage would have satisfied. A source whose every stage is a hook
is valid, and its spec carries little more than an identity.

### 3.4 Extractor

An Extractor reads one value from one document.

```python
Extractor:
    css:      str | None = None
    json:     str | None = None
    regex:    str | None = None
    header:   str | None = None
    const:    Any | None = None

    attr:     str | list[str] = "text"
    all:      bool = False
    pipe:     PipeRef | None = None
    fallback: list[Extractor] = []
    default:  Any | None = None
```

`const` and `header` MUST NOT be combined with any other source. Otherwise `css` MAY be combined with
`json` or `regex`, in which case `css` locates the element and the other reads its content. If no
source key is present, the value is the node currently in scope, which is how a field reads the row it
is already inside.

A `json` path of `$` denotes the whole parsed body, which is how an API returning a bare array at the
top level is selected from. Dotted segments and numeric indices address into it, so `$`, `0.id` and
`props.pageProps.title` are all valid.

`regex` follows the same rule as the `regex` step in §6.1: it yields **capture group 1** when the
pattern declares a group, and the whole match when it does not. Anything else is a trap, because
`regex: 'id=(\d+)'` plainly means the number. Reading a later group is a `regex{pattern, group}` step.

Combining `css` with `json` is what reads a page's structured data, which is usually inside a
`<script>` element. Pages carry many scripts, so the selector names the one:

```yaml
title:
  css: "script#__NEXT_DATA__"
  json: props.pageProps.novel.title
```

| Field      | Meaning                                                                                                                                                                                             |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `css`      | A selector, evaluated relative to the node in scope. See §3.4.1.                                                                                                                                    |
| `json`     | A dotted path into a JSON response body, or into the element `css` selected.                                                                                                                        |
| `regex`    | A regular expression applied to the document's raw text, or to the element `css` selected.                                                                                                          |
| `header`   | A response header, by name, from the response its enclosing stage's request produced.                                                                                                                |
| `const`    | A literal. Interpolation still applies, so this is how a field is produced from `vars` alone when the page does not carry it.                                                                        |
| `attr`     | `text`, `html` (the element's **inner** markup), `outer_html`, or attribute names. A list is tried in order and may mix them, so `[title, text]` means "the title attribute, or the element's text". |
| `all`      | Produce a list of every match instead of the first.                                                                                                                                                 |
| `pipe`     | Transforms to apply. See §6. Absent means the field's default pipe.                                                                                                                                 |
| `fallback` | Whole alternative Extractors, tried in order while the result is empty.                                                                                                                             |
| `default`  | Used when everything else produced nothing.                                                                                                                                                         |

`attr` accepts a list because trying `data-lazy-src`, then `data-src`, then `src` is the normal case
for a lazily loaded image.

**An undeclared `attr` yields whatever the pipe consumes.** Its default reads as `text`, but when the
effective pipe's first step consumes a node the selected element is passed through unchanged instead.
Without this rule the most important field in the format cannot be written: `body: { css: "#content" }`
would turn the element into a string before `paragraphs` ever saw it, and every paragraph boundary
would be gone. A spec that genuinely wants the text of an element before a node step writes
`attr: text` explicitly, and the type mismatch is then reported under §6.1.

`fallback` is how a source survives a redesign. A maintainer SHOULD add a new selector ahead of the
old one rather than replacing it, so a stale or partially deployed page still reads correctly and a
wrong guess degrades instead of breaking.

#### 3.4.1 Selector syntax

Implementations MUST support CSS Selectors Level 3 syntax [SELECTORS], plus the three additions named
below and nothing else. A selector that works in one implementation and not another defeats the
purpose of a shared format, so implementations MUST NOT invent further extensions.

`:-soup-contains("text")` is not standard CSS. It originates in soupsieve [SOUPSIEVE], and its
semantics are normative here: it matches an element whose text content, **including descendants**,
contains the given substring, compared **case-sensitively** and without normalising whitespace.
Leaving those three choices to the implementation would let the same selector match different elements
in different readers.

`:has(S)` and `:scope` are standard, but they are Selectors Level 4 [SELECTORS4] rather than Level 3,
so requiring them has to be said rather than assumed. `:has(S)` matches an element for which the
relative selector `S` matches at least one element, and `S` MAY begin with a combinator, so
`:has(> h1)` tests direct children only. `:scope` matches the node the selector is being evaluated
against, which is the only way to write "a direct child of the node in scope", as in `:scope > div`.

All three reach things nothing else can. A page's data script often has no `id` or `class`, so it can
only be identified by what is inside it, as in `script:-soup-contains("var chapImages")`. A value is
frequently identified only by an adjacent label, as in `strong:-soup-contains("Author:")`. And a CSS
match otherwise yields only the element matched, so reading a label's parent requires inverting the
match with `:has`.

### 3.5 Var

```python
Var(Extractor):
    on:    Literal["url", "novel", "chapter"] | Request = "novel"
    renew: bool = False
```

A var is a named Extractor whose result is available to templates as `{vars.<name>}`. `on` declares
what it reads:

| `on`        | Reads                                                       |
| ----------- | ----------------------------------------------------------- |
| `url`       | The novel URL string itself, with no document fetched.      |
| `novel`     | The novel page.                                             |
| `chapter`   | The chapter page.                                           |
| a `Request` | Its own request. The result MUST be cached for the session. |

An implementation MUST evaluate a var lazily and cache it **for the lifetime of what it reads**:

| `on`           | Cached for      |
| -------------- | --------------- |
| a `Request`    | the session     |
| `url`, `novel` | the novel       |
| `chapter`      | the one chapter |

Caching a chapter-scoped var for the session would reuse a value read from the first chapter for every
later one, which reads as intermittent site trouble rather than as a caching bug.

**`renew` says what to do when a cached value goes stale.** With `renew`, an implementation MUST
discard the cached value, evaluate the var again when a request using it is refused, and retry that
request once. Session credentials expire and a long crawl outlives them: a token read at chapter 1 may
be dead by chapter 400, and every remaining chapter then fails for a reason that looks like the site
blocking us.

Without `renew` a stale var is an error, which is the right default. Retrying on every failure would
mask a wrong selector behind repeated requests.

### 3.6 Request

Everything about _making_ a request lives here. Everything about _reading_ the response lives on the
stage. The two vocabularies stay in separate namespaces, so `request.payload` is a body and
`items.json` is a path, and neither can be mistaken for the other. An implementation SHOULD assert that
no type reuses a field name from a type it composes, as a cheap backstop against the two vocabularies
colliding again.

```python
Request:
    name:     str | None = None
    get:      UrlTemplate | None = None
    post:     UrlTemplate | None = None
    payload:  dict = {}
    form:     str | None = None
    headers:  dict[str, str] = {}
    encoding: str | None = None
    render:   bool = False
    wait_for: str | None = None
    page:     str | None = None            # a stage name or a declared request name
    from:     list[Request] = []
    paginate: Paginate | None = None
```

Exactly one of `get`, `post`, `page` and `from` MUST be present, **except where the stage supplies a
default address.** `novel` defaults to a GET of the novel URL and `chapter` to a GET of the URL the
table of contents captured, so either MAY declare a request that sets only `paginate`, `headers`,
`encoding`, `render` or `wait_for` and inherits its address from the stage. `search` and `toc` have no
default and MUST name one of the four.

That exception is what expresses a chapter body spanning several pages: such a chapter declares
`paginate` and nothing else, because its first page is the URL the table of contents already produced.

`payload` is the request body. One field holds it whatever the wire format, because the body is one
concept. It is valid with `post`, and with `get` when `form` is also set, which is the two-step
described below.

**Its encoding is inferred from its shape, and an explicit `content-type` header overrides that.** A
payload containing a nested object, a list, a boolean or a number cannot be form-encoded, so it is sent
as JSON. A payload of flat strings is sent form-encoded, which is what most sites accept and what a
`form` selector implies.

```yaml
payload: { translate: web, retry: false }     # JSON: a boolean cannot be form-encoded
payload: { searchkey: "{query}" }             # form-encoded: all flat strings
```

The one case inference reads wrongly is an API whose body happens to be flat strings, and that spec
says so with a header:

```yaml
payload: { text: "{query}" }
headers: { content-type: application/json }
```

`page` reuses a document already fetched in this operation, which avoids a second request for the
common case where the chapter list is on the novel page.

It takes a name. Every stage's own request is implicitly named after its stage, so `page: novel` needs
no declaration, and any request MAY declare a `name` to be referenced the same way. Names MUST be
unique within a resolved spec and MUST NOT shadow a stage name.

A `page` reference MUST resolve to a request that has already run when it is evaluated, and an
implementation MUST reject one that does not rather than fetching it a second time. Stages run in the
order `search`, `novel`, `toc`, `chapter`, so a chapter may reuse the novel document but not the
reverse.

`form` is a selector for a form element. Every `input` it contains is harvested into `payload` by
name, and `payload` is then applied over the result, so a spec supplies only the field it cares about.
Its presence implies form-encoding.

This serves a site whose form body carries hidden inputs, a verification token or a per-visit state
blob. Those values cannot be hardcoded and enumerating them is guesswork, so the only way to send them
is to read them off the form.

**Which document holds the form depends on the other keys, and both readings are needed.**

| With   | The form is read from          | The request goes to |
| ------ | ------------------------------ | ------------------- |
| `post` | the document already in scope  | the `post` URL      |
| `get`  | the document the `get` fetches | the form's `action` |

The `post` reading serves a table of contents or a chapter, where the novel page is already in scope.
The `get` reading serves a `search` stage, which has no document in scope at all: the form and its
token exist only on a page the stage must fetch first. Without it a whole family of sites would need a
hook to do something entirely ordinary.

```yaml
search:
  request:
    get: "{origin}/search.html" # fetch the page holding the form
    form: ".search-container form" # harvest its inputs and its action
    payload: { keyboard: "{query}" } # applied over them
```

`render` runs the page's scripts before it is parsed, for a site whose content does not exist in the
served markup. `wait_for` is a selector to wait for before reading, and SHOULD be set whenever
`render` is, because "the scripts have finished" is otherwise a guess about timing.

A rendered fetch is far slower and heavier than an HTTP one. An implementation SHOULD report when a
spec uses it, and a spec SHOULD NOT set it on a stage that works without it.

`encoding` governs the request as well as the response. Values in `form` MUST be encoded with it before
percent-encoding, and the response MUST be decoded with it. A site serving a legacy encoding generally
expects its search terms in that encoding too, and sending a UTF-8 query to such a site returns results
for a different string, silently.

`from` holds alternatives tried in order until one yields items. This is how a site with more than one
possible chapter-list endpoint is described without conditionals.

Two rules make it useful rather than decorative, because a site that has only one of the endpoints is
the entire reason to write a list.

**Any failure moves to the next alternative.** Not only a refusal the implementation models: a
transport error, a rejected status and a body that will not parse all count. An endpoint absent from
this installation answers `404`, and treating that as fatal means the first alternative decides the
stage. When every alternative fails, the error MUST name what each one did, since a spec author cannot
otherwise tell a wrong address from a blocked one.

**An alternative that fetched but yielded nothing loses to the next one.** Where the stage has an
`ItemList`, "yields items" means that list produces at least one row; a stage with no item list has no
test and the first alternative that fetched is used. An ajax endpoint that answers `200` with an empty
body is common enough that testing only reachability would report zero chapters for a novel whose list
is sitting on the page named further down.

### 3.7 Paginate

```python
Paginate:
    while:      Literal["has_items"] | None = None
    first:      int | Extractor = 1
    step:       int = 1
    last:       int | Extractor | None = None
    next:       Extractor | None = None
    url:        UrlTemplate | None = None
    concurrent: bool | None = None
```

Exactly one of `while`, `last` and `next` MUST be present.

| Termination        | Use when                                                                 |
| ------------------ | ------------------------------------------------------------------------ |
| `while: has_items` | The number of pages is unknown. Stop at the first page yielding nothing. |
| `last`             | The last page's number is readable from the first page, or known.        |
| `next`             | The document links to the next page.                                     |

`url` is REQUIRED with `while` and `last`, and MUST NOT be set with `next`, which takes its URL from
the extracted link.

**`url` produces the second page onward.** The first page comes from the stage's own `get`, `post` or
`page`. Sites routinely give the first page a different address from the rest, `/chapters` then
`/chapters/page-2`, and a scheme that generated `page-1` would either 404 or silently serve a
duplicate.

`concurrent` controls whether pages are fetched in parallel. **Left unset it is on wherever the
termination condition allows**, which is `last` and `while`: a list addressed by page number has no
reason to be walked one round trip at a time, and reading the chapter list is what a crawl spends most
of its time on. Setting it `false` forces one request at a time.

It never applies with `next`, whose pages are known only one at a time because each address comes from
the page before it. An unset `concurrent` there is simply inapplicable, while an explicit `true` MUST
be rejected: the author has misunderstood the mechanism, and that is worth saying rather than ignoring.

An implementation MUST continue to honour the host's rate limit when fetching concurrently.
Parallelism decides how many requests may be waiting on that budget, never how large it is.

With `while` the walk is necessarily speculative, since nothing says where the list ends. An
implementation MAY request a window of pages together, and then MUST keep them only up to the first
that yielded no items, discarding any beyond it even if those hold rows. A page kept from past the end
would insert a gap into the chapter list. The cost is a bounded number of wasted requests, at most one
window, which is why `last` remains preferable wherever a site reports where its list ends.

**`first` and `last` are the numbers the site puts on its own pages, not a count of them.** The
stage's own request already produced `first`, so the pages fetched through `url` are the ones after it,
in steps of one, up to and including `last`. Either may be written as a literal or read from the first
page, and where an extractor produces several numbers the largest wins, because a pager lists the pages
it can reach and the last of those is what bounds the walk.

`first` defaults to 1. A site numbering from zero sets `first: 0`, and getting that wrong fails **while
reporting success**: the walk starts one page late and loses those chapters with every field still
producing something.

There is no separate cap on the number of pages. `last` already bounds the range, so a second bound
would be a way of saying the same thing twice, and a caller that wants a tighter one for its own
reasons applies it without the spec declaring anything. An implementation SHOULD report when it
truncated a walk, because a silent cap is indistinguishable from a site with fewer pages.

#### 4.5.1 Stride

`step` is how much `{page}` advances between pages, defaulting to 1. The sequence is `first`,
`first + step`, `first + 2 × step`, and so on, bounded by `last` or by `while`.

A site that addresses a page by the **index of its first row** rather than by a page number sets `step`
to the page size. `{page}` then counts rows:

```yaml
paginate:
  first: 1
  step: 100
  last: { css: "#total" }
  url: "{origin}/feeds/posts/summary?alt=atom&max-results=100&start-index={page}"
```

`step` MUST be at least 1. It does not affect concurrency: every address is computable from `first`,
`step` and `last` whatever the stride.

A page size the host does not honour breaks this, because the stride and the number of rows returned
then disagree and the walk skips those it was not given. The page size a spec names MUST therefore be
one the host serves in full.

`paginate` is the only iteration in this format. See §12.

### 3.8 Stages

```python
SearchStage(ItemList):
    # fields carries: title, url, info


NovelStage:
    request:  Request | None = None
    title:    Extractor | None = None
    cover:    Extractor | None = None
    authors:  Extractor | None = None
    tags:     Extractor | None = None
    synopsis: Extractor | None = None
    language: Extractor | None = None


TocStage:
    request: Request | None = None
    items:   ItemList | None = None
    volumes: ItemList | None = None


ChapterStage:
    request: Request | None = None
    url:  Extractor | UrlTemplate | None = None
    body: Extractor | None = None
    join: str = ""
```

```python
ItemList:
    request: Request | None = None
    css:     str | None = None
    json:    str | None = None
    sort_by: str | None = None
    reverse: bool = False
    fields:  dict[str, Extractor] = {}
    require: list[str] = []
```

`ItemList` describes a repeated structure: a container selected by `css` or `json`, and per-row
Extractors in `fields`, each evaluated with that row in scope.

**`json` and `css` together mean parse, then select.** When both are present, `json` reads a value
that is markup rather than structured data, and it MUST be parsed as a document before `css` runs.
This is how an API answering with an HTML fragment inside a JSON field is read, which many sources
need: a search endpoint returning `{"resultview": "<li>..."}`, or a WordPress endpoint returning
`content.rendered`.

```yaml
search:
  request: { post: "{origin}/search" }
  json: resultview # a string of markup
  css: ".novel-item a" # parsed, then selected
  fields:
    title: { css: .novel-title }
```

The parse is implied rather than written because there is nothing else `css` could mean against a
string, and the alternative is every such source declaring the same single step. It is the same
reasoning as §6.4's rule that a node-consuming default applied to text gets `parse_html` prepended.

`sort_by` names a field to order rows by. Comparison is **numeric**, and a row whose value does not
parse as a number sorts after every row that does, keeping its relative position among the others.
`reverse` is applied after it, so the two compose. Both are OPTIONAL and document order is the default.

Numeric only, because the field exists for chapter and volume numbers. Inferring the comparison from
the data would let one malformed row silently change how every other row compares.

Ordering by a number a row carries is correct whatever order the site chose to present, and it needs no
branch. Sites list chapters newest-first, oldest-first, and sometimes in an order that depends on a
theme setting.

`fields` MAY contain keys beyond those a stage names. Extra keys MUST be preserved on the produced item
and MUST be readable later as `{chapter.<key>}`. This is how a chapter carries an identifier from the
table of contents to the request that fetches it.

**An item whose required field resolves empty MUST be skipped, not emitted.** A chapter needs a `url`,
a search result needs a `url`, a volume row needs nothing. Skipping rather than emitting a broken item
is what lets a selector be written loosely and then narrowed with a pipe:

```yaml
items:
  css: 'a[href*="/chapters/"]'
  fields:
    url:
      attr: href
      pipe: [{ regex: { pattern: '.*/chapters/[^/]+/\d+-.*' } }]
```

Anchors that do not match yield no `url` and disappear. This is what keeps navigation links, a "latest
chapters" panel sharing the list's class, or a site menu out of a chapter list without needing a
perfect selector or a hook.

Implementations SHOULD report how many items were skipped. A large number means the selector is wrong
even though the crawl succeeded.

**`require` names further fields a row cannot do without.** A row where any of them resolves empty MUST
be skipped by the same rule, and the count MUST be reported the same way. `require` adds to what the
stage requires and MUST NOT remove it: a chapter still needs a `url`.

This rejects a row on a field the stage does not use. The condition lives in that field's pipe: a filter
step yields nothing when it rejects (§6.2), so the field resolving empty is the rejection.

```yaml
items:
  json: "$"
  require: [parent]
  fields:
    title: { json: name }
    url: { json: link }
    parent: { json: parent, pipe: [{ reject: { pattern: "^[1-9]" } }] }
```

A category with a non-zero `parent` is a section of a novel rather than a novel, and neither its `title`
nor its `link` says so.

An implementation MUST reject, at load time, a `require` naming a field the list does not declare.

**A `volumes` row has no required field, so the skip rule does not protect it.** An over-broad `items`
selector self-corrects; an over-broad `volumes` selector silently produces junk volumes with empty
titles. Implementations SHOULD therefore skip a volume row that yields no `title`, and SHOULD report
the count.

**A field MAY be a `UrlTemplate` referring to sibling fields as `{item.<name>}`.** Fields are evaluated
in declaration order, so a template may reference any field declared before it. This is how a row that
carries identifiers rather than a link produces one:

```yaml
items:
  css: .row
  fields:
    serie_id: { json: raw_id }
    slug: { json: slug }
    url: "{origin}/en/serie-{item.serie_id}/f{item.slug}"
```

`ChapterStage.url` accepting a template is the same rule applied one stage later.

`NovelStage.request` defaults to a GET of the novel URL, so a site whose details live behind a POST or
an API call needs no special case. Every field is optional because the interpreter SHOULD fall back to
standard page metadata, in the order OpenGraph, JSON-LD, then the document title, before giving up. A
spec declares a field only when the site does not describe itself correctly.

`NovelStage.language` reads a language per novel, for hosts serving more than one.

`TocStage` MUST have `items`. `volumes` is OPTIONAL and never a source of chapters.

| Shape                                                    | How it is expressed                        |
| -------------------------------------------------------- | ------------------------------------------ |
| Chapters grouped every N                                 | Nothing. `chapters_per_volume` handles it. |
| Volume headers interleaved with chapter rows as siblings | `volumes`                                  |
| A volume number readable on each chapter row             | A `volume` field in `items.fields`         |
| Chapters nested inside a volume element                  | Not supported. Use a `toc.items` hook.     |

Most sources declare nothing here: a volume every hundred chapters is what `chapters_per_volume` does
automatically.

When `volumes` is present, its selector and `items`' selector MUST run over the same container, and
each chapter MUST be assigned the nearest **preceding** volume row in document order. Chapters before
the first volume row belong to an implicit first volume. This is the shape real sites use: a flat list
where a heading row partitions the chapters that follow it.

When `items.fields` contains `volume`, its value is the volume number for that chapter and takes
precedence over positional assignment. `volumes` MAY still supply titles for those numbers.

`ChapterStage` has a `request` like every stage, so a chapter body MAY span several pages via its
`paginate`, and extracted bodies are concatenated in order using `join`.

`ChapterStage.url` defaults to the URL captured by the table of contents.

### 3.9 Scalar types

```python
Step     = str | dict[str, Any]
PipeRef  = str | list[Step]
RepoPath = str
UrlTemplate = str
HookPoint = str        # "<stage>.<name>", or a session point. See §3.9.2.
```

A `Step` is a bare step name, a single-key mapping of name to arguments, or `{hook: <path>}`:

```yaml
pipe:
  - trim                                    # a bare name
  - { strip_prefix: "Author:" }             # one argument
  - { regex: { pattern: '\d+', group: 0 } } # named arguments
  - { hook: hooks/shared/fix_ruby.py }      # a hook as a step
```

A `PipeRef` is a name from `pipes`, an inline list of steps, or a list mixing both.

A `RepoPath` is a repository-relative path that MUST exist and MUST resolve by opening it. There is no
search path, no name-to-path mapping, and no filename heuristic. This makes every reference identical
across implementations and clickable in an editor.

#### 3.9.1 Key names

Keys are spelled exactly as this document spells them. `while` and `from` are reserved words in some
implementation languages, and an implementation MAY name its own attribute whatever it must, commonly
by appending an underscore. **That name is private to the implementation and is not a key.** A key
carrying a trailing underscore MUST be rejected.

```yaml
paginate: { while: has_items }    # correct
paginate: { while_: has_items }   # rejected
```

An interpreter MUST reject a key it does not recognise, at every node of the model, at load time, in
an error naming the key. It MUST NOT ignore one.

#### 3.9.2 Hook points

A hook point is `<stage>.<name>`, where `<stage>` is a stage this document defines and `<name>` is
either one of that stage's own fields or `request`, which replaces the whole fetch. Two further points
belong to the session rather than to a stage and are named without a prefix:

| Point             | Replaces                                           |
| ----------------- | -------------------------------------------------- |
| `<stage>.<field>` | Producing that field's value                       |
| `<stage>.request` | Producing that stage's document                    |
| `check_response`  | Deciding whether a response is a refusal or a page |
| `login`           | Authenticating the session                         |

So the complete set for §3.8's stages is `search.request`, `search.items`, `novel.request`,
`novel.title`, `novel.cover`, `novel.authors`, `novel.tags`, `novel.synopsis`, `novel.language`,
`toc.request`, `toc.items`, `toc.volumes`, `chapter.request`, `chapter.url`, `chapter.body`, plus the
two session points.

The set is closed, because the stages and their fields are. It is derived from them rather than listed
a second time, so the two can never disagree and a hook point always carries the name of the field it
produces.

An implementation MUST reject a hook point that names an unknown stage or a field that stage does not
define, at load time.

### 3.10 JSON Schema

An implementation MUST publish a JSON Schema [JSONSCHEMA] for this model. The schema is generated from
the model, and CI MUST fail if a regenerated schema differs from the committed one, so the two cannot
drift.

The schema validates a raw document, in which any key may be `null` to delete an inherited value
(§5.1). Every property except `spec` MUST therefore admit `null`, whatever the field's own type.

## 4. Evaluation semantics

### 4.1 Extractor order

An Extractor MUST be evaluated in this order:

1. Resolve the source (`css`, `json`, `regex`, `header`, `const`, or the node in scope).
2. Apply `all`.
3. Apply `attr`, unless it is undeclared and the pipe's first step consumes a node (§3.4).
4. Apply `pipe`.
5. If the result is empty, apply `default`.
6. If the result is still empty, try each `fallback` in order, each evaluated in full by these same
   rules.

`all` resolves before `pipe` so that a pipe maps element-wise over a list (§6.3). Reversing those two
changes the meaning of a list-valued field with a text pipe, so the order is normative rather than
incidental.

"Empty" means absent, `None`, an empty string after trimming, or an empty list.

Given the same document, an Extractor MUST produce the same result every time.

### 4.2 Interpolation

A `UrlTemplate`, and every value in `payload` and `headers`, MAY contain placeholders. The set is
closed. There are no expressions, no arithmetic and no conditionals.

| Placeholder                | Available in                                                    |
| -------------------------- | --------------------------------------------------------------- |
| `{origin}`                 | Everywhere. The scheme and host of `base_url`.                  |
| `{vars.*}`                 | Everywhere, subject to each var's own scope.                    |
| `{query}`                  | `search`                                                        |
| `{novel_url}`              | `novel`, `toc`, `chapter`                                       |
| `{request_url}`            | Any `paginate.url`. The URL the stage's own request resolved to |
| `{page}`                   | Any `paginate.url`. The page's number, or its first row's index |
| `{chapter.*}`              | `chapter`, including extras captured by `toc` fields            |
| `{item.*}`                 | Inside an `ItemList` field, a sibling field declared earlier    |
| `{username}`, `{password}` | The `login` hook only                                           |

Filters apply with a pipe character and compose left to right, as in `{query|lower|plus}`. The set is
closed:

| Filter           | Effect                                                             |
| ---------------- | ------------------------------------------------------------------ |
| `plus`           | Spaces become `+`. Nothing else is escaped.                        |
| `urlencode`      | Percent-encode per RFC 3986, spaces as `%20`                       |
| `urlencode_plus` | Percent-encode, spaces as `+`                                      |
| `lower`          | Lower case                                                         |
| `slug`           | Lower case, non-alphanumerics collapsed to single hyphens, trimmed |

Three encodings rather than one because sites genuinely differ, and collapsing them silently sends the
wrong query.

`{request_url}` exists because a paginated stage usually builds later pages from the address it already
fetched, which may have been derived from a var or a redirect rather than written in the spec.

**A var's own request sees only what precedes it.** A `Var` whose `on` is a `Request` (§3.5) MAY use
`{origin}` and any var that does not depend on it. It MUST NOT use `{query}`, `{novel_url}`,
`{chapter.*}`, `{item.*}` or `{page}`, because such a var is session-scoped and outlives every one of
them. It MUST NOT use `page:` to reference a stage document either: a var may be evaluated while the
document that would satisfy it is still being read, and the resulting cycle is not detectable at run
time. Implementations MUST reject both at load time.

An unknown placeholder or filter MUST be a load-time validation error.

**A rendered `UrlTemplate` has a doubled slash in its path collapsed.** A template cannot know whether
the placeholder before its literal `/` already ends in one, so `{novel_url}/ajax/chapters/` is the
natural way to write that request and resolves to `.../a-title//ajax/chapters/` for a novel URL with a
trailing slash. Sites disagree about whether the two are the same address and enough answer the doubled
form with a `404` that leaving it to the author means every such template carries the same bug. Only
the path is affected: a query string keeps its slashes, because a `//` there can be data. This applies
to `UrlTemplate` alone, never to a `payload` or `headers` value.

### 4.3 Relative URLs

Every URL an Extractor produces MUST be resolved against the URL of the document it came from, not
against `base_url`. A site that paginates into a subdirectory, or serves content from a second host,
produces wrong links otherwise.

### 4.4 Failure

A stage failing to produce a required value MUST raise an error identifying the **spec field**
responsible, its file, and its line where available. `toc.items.url matched 0 nodes` is actionable; a
parser traceback is not.

Missing `title` or an empty chapter list MUST be errors. A missing cover, author, tag list or synopsis
SHOULD be a warning, because they are absent from real pages often enough that failing would reject
working sources.

### 4.5 Concurrency and determinism

Sources run concurrently at two levels. Several jobs crawl different novels at once, subject to a
per-domain cap, and within one crawl a pool of worker threads fetches chapters and table-of-contents
pages in parallel. What is shared and what is not:

| Shared                | Lifetime                                                       |
| --------------------- | -------------------------------------------------------------- |
| the resolved spec     | loaded once, used by every crawl of that host                  |
| hook modules          | imported once, called from every crawl and every worker thread |
| `ctx` and `ctx.state` | one per crawl, but touched by that crawl's worker threads      |
| `vars` caches         | per their scope, read concurrently                             |

Five requirements follow.

**A resolved spec MUST be immutable.** It is shared across concurrent crawls, so an implementation MUST
NOT let interpretation mutate it, and MUST NOT store per-crawl values on it.

**Var evaluation MUST be single-flight.** When several threads reach an unevaluated var together,
exactly one MUST evaluate it and the others MUST wait for that result. Without this, a token-bearing
var backed by its own request is fetched once per worker thread, which is both wasteful and a good way
to be rate-limited on the first page of every crawl.

`renew` MUST be single-flight for the same reason and more urgently: a credential expiring mid-crawl
refuses every in-flight request at once, and N threads each renewing independently is a burst against
the one endpoint that must not be antagonised.

**`ctx.state` MUST be safe for concurrent access**, and an implementation MUST provide a way to update
it atomically rather than leaving hooks to attempt read-modify-write. A hook is called from several
threads of the same crawl.

**Concurrent pagination MUST preserve page order.** With `concurrent: true`, pages complete out of
order, and results MUST be assembled by page index rather than by completion. Assembling by completion
would number chapters differently on every run, and the numbering is what the rest of the system stores
and compares.

Determinism is the point of all five. §4.1 requires that the same document yields the same value; these
extend that to the same crawl yielding the same novel regardless of thread timing.

## 5. Resolution semantics

### 5.1 Merging

`extends` names one parent. Resolution merges the parent's resolved document with the child's raw
document, before validation:

| Case             | Rule                                                   |
| ---------------- | ------------------------------------------------------ |
| Scalar           | The child's value replaces the parent's.               |
| Mapping          | Merged key by key, recursively.                        |
| `fallback` lists | The child's entries are **prepended** to the parent's. |
| Any other list   | The child's list replaces the parent's.                |
| Explicit `null`  | Deletes the inherited key.                             |

Prepending is what makes `fallback` inheritable: a child adds a selector its own site needs while
keeping everything the parent knew, and order is already "try until one works" so inserting at the
front is meaningful.

`pipe` replaces, like every other list. Step order in a pipe is semantic, so a child adding `trim`
would have it run before the parent's `paragraphs`, which is almost certainly not what it meant. A child
wanting the parent's steps plus its own either writes both, or references the parent's named pipe
(§6.4) as a step in its own.

### 5.2 Limits

Implementations MUST reject a cycle in `extends`, and MUST reject a chain deeper than an
implementation-defined limit, which SHOULD be at least 8.

`extends` MAY name a concrete spec or an abstract one. Extending a concrete sibling is how a mirror is
expressed, and it means a change to that sibling reaches every mirror, which is both the intent and the
risk.

`extends` MUST NOT name a spec in `disabled/`. Disabling a spec with dependents would otherwise
silently orphan them.

### 5.3 Introspection

An interpreter MUST be able to print a fully resolved spec. With inheritance, "what am I actually
running" has to be answerable in one command, or a deep chain becomes undebuggable.

## 6. The transform registry

Cleaning is an operation, not a place. A title may need a site suffix stripped, an author name a prefix
removed, a tag list junk dropped, a synopsis a heading removed. Those are the same kind of work, so
**any extracted value MAY carry a `pipe`** and there is no separate cleaner.

```yaml
novel:
  title: { css: h1, pipe: [trim, { strip_suffix: " - Read Online" }] }
  authors: { css: .author, all: true, pipe: [{ strip_prefix: "Author:" }, trim, drop_empty] }
```

### 6.1 Steps

Every step declares what it consumes and produces. At load time an implementation MUST reject a pipe
whose types do not connect, and MUST reject a step naming nothing in the registry, in an error naming
the step. Both checks run on the **resolved** document, with named pipes (§6.4) expanded.

A pipe's input kind is `list` where the Extractor sets `all`, and otherwise unconstrained: what an
Extractor hands its pipe depends on `attr` and on §3.4. The type check therefore constrains the second
step onward.

| In → out          | Steps                                                                                                                                                                                   |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| node → node       | `strip_tags(names)`, `strip_css(selectors)`, `unwrap(names)`, `unwrap_all`, `keep_attrs(names)`, `unlazy_images`, `drop_leading{matches, within}`, `drop_empty_nodes`, `strip_matching{pattern, tags}`                   |
| node → html       | `paragraphs{block_tags, preserve}`, `inner_html`                                                                                                                                        |
| text → node       | `parse_html`                                                                                                                                                                            |
| node → text       | `text`                                                                                                                                                                                  |
| text → text       | `trim`, `collapse_spaces`, `lower`, `title_case`, `normalize_unicode{form}`, `strip_prefix(s)`, `strip_suffix(s)`, `replace{pattern, with}`, `regex{pattern, group}`, `reject{pattern}` |
| text → list       | `split(sep)`                                                                                                                                                                            |
| list → list       | `drop_empty`, `unique`                                                                                                                                                                  |
| list → text       | `join(sep)`, `max`, `min`                                                                                                                                                                      |
| list[text] → html | `lines_to_html{tag, attr}`                                                                                                                                                              |

Steps absent from the table below do exactly what their name says. These do not, either because they
take parameters that need a default or because a plausible reading of the name is the wrong one. An
implementation MUST follow these definitions: an unspecified step makes the same document behave
differently in the interpreter, the web preview and any other reader.

| Step                            | Behaviour                                                                                                                                                                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `text`                          | The text of the node and its descendants, concatenated with no separator inserted between them. Identical to `attr: text`.                                                                                    |
| `inner_html`                    | The node's children, serialised. The node's own tag is not included. Identical to `attr: html`.                                                                                                               |
| `strip_tags(names)`             | Removes each element having one of these tag names, **including its content**.                                                                                                                               |
| `strip_css(selectors)`          | The same, selecting by selector rather than by tag name.                                                                                                                                                      |
| `strip_matching{pattern, tags}` | Removes each element whose text matches `pattern`, **including its content**. Where `tags` is given, only elements having one of those names are considered; where it is absent, only elements having no element children are. An element containing a match also contains the matching text, so an unrestricted search would remove an ancestor of the intended target. Removes nothing when `pattern` is absent. |
| `unwrap(names)`                 | Replaces each element having one of these names with its children, **keeping its content**.                                                                                                                   |
| `unwrap_all`                    | Unwraps every descendant, leaving the node's text with no markup inside it.                                                                                                                                   |
| `keep_attrs(names)`             | Removes every attribute except these, from the node and from every descendant.                                                                                                                                |
| `unlazy_images`                 | For each image element, sets `src` to the first of `data-lazy-src`, `data-src`, `src` that is present and non-empty, then removes the other two. The order is normative: reversing it selects the placeholder the site is deferring away from. |
| `drop_empty_nodes`              | Removes every element whose text is empty or whitespace and which contains no image.                                                                                                                          |
| `drop_leading{matches, within}` | Tests the text of the node's first `within` element children against `matches` and removes the first that matches, at most one, provided that child is plausibly a heading. `within` defaults to 1. A child that contains block-level elements of its own, or that holds almost all of the node's text, MUST NOT be removed: some themes wrap a whole chapter in one element opening with its title, and this step is a cleanup (§6.2), so where there is nothing safe to do it changes nothing. |
| `collapse_spaces`               | Replaces every run of whitespace with a single space. Line breaks, tabs and no-break spaces count as whitespace.                                                                                               |
| `title_case`                    | Upper-cases the first character of each whitespace-separated word and leaves the rest of each word unchanged, so an acronym or a deliberately lower-case word survives.                                        |
| `normalize_unicode{form}`       | Applies Unicode normalisation. `form` is one of `NFC`, `NFD`, `NFKC`, `NFKD`, and defaults to `NFKC`.                                                                                                         |
| `regex{pattern, group}`         | Yields capture group `group`, defaulting to 1, or the whole match when the pattern declares no group. Yields nothing when the pattern does not match.                                                          |
| `reject{pattern}`               | The inverse of `regex`: yields nothing when the pattern matches, and the value unchanged otherwise. With §6.2 this is how unwanted rows are dropped.                                                          |
| `split(sep)`                    | Splits on every occurrence of `sep`. Empty entries are kept, and `drop_empty` is what removes them.                                                                                                            |
| `drop_empty`                    | Removes entries that are empty or whitespace only.                                                                                                                                                            |
| `unique`                        | Removes later duplicates and **preserves first-appearance order**.                                                                                                                                            |
| `max`                           | The highest numeric entry in a list, ignoring entries that are not numbers. This reads the last page's number off a pager, whose final link is often a "next" label rather than a number. The number keeps the spelling it had, since it goes back into a URL and a site with `page=007` has no `page=7`. |
| `min`                           | The lowest numeric entry, by the same rules. This reads which page a site calls its first, for `paginate.first`.                                                                                               |
| `parse_html`                    | Turns a string into a document, so the rest of a pipe or an `ItemList` selector can work on it. This is what reads an HTML fragment delivered inside a JSON field.                                             |
| `lines_to_html{tag, attr}`      | Wraps each entry of a list in an element. With `attr` the value goes into that attribute instead of becoming text, so `lines_to_html{tag: img, attr: src}` turns a list of image URLs into image elements.     |
| `paragraphs{block_tags, preserve}` | See below.                                                                                                                                                                                                |

`paragraphs` is worth stating at length, because it is the default for both `synopsis` and
`chapter.body` and so decides what most chapter text looks like. It walks the node's children and
classifies each one:

- A comment, a `script` or a `style` is discarded.
- Text is kept as it stands.
- An element named in `preserve` is emitted whole, with its attributes, and is not descended into.
- An element named in `block_tags`, and also `br` and `hr`, ends the current paragraph and starts the
  next.
- Any other element is descended into and **kept as a tag** around whatever its content produced, so
  inline formatting such as `b` or `em` survives.

The result is that sequence of paragraphs, each wrapped in `p`, with any paragraph holding neither text
nor an image dropped. `block_tags` defaults to `article`, `aside`, `div`, `h1` through `h6`, `main`,
`p`, `section`. `preserve` defaults to `img`, `pre`, `canvas`.

### 6.2 A step that does not apply

No step raises because it found nothing to do. What it yields instead depends on whether it is a
filter or a cleanup, and the difference is normative because getting it wrong loses data silently.

| Kind               | Steps                                                                                                   | With nothing to do          |
| ------------------ | ------------------------------------------------------------------------------------------------------- | --------------------------- |
| **Filter**         | `regex`, `reject`                                                                                       | MUST yield an empty value   |
| **Optional cleanup** | `strip_prefix`, `strip_suffix`, `replace`, `strip_tags`, `strip_css`, `unwrap`, `drop_leading`, `unlazy_images`, `drop_empty_nodes`, `keep_attrs` | MUST yield the value unchanged |

A filter yielding nothing is what removes conditionals from cases that would otherwise need them. A
site that reuses one link for "next page of this chapter" and "next chapter", distinguished only by
URL shape, is expressible as pagination whose `next` extractor ends with a `regex` matching only the
first form. A non-match yields nothing, pagination sees no next page, and it stops.

A cleanup MUST NOT behave that way, because "remove this if it is there" is what a spec means by it.
A tag list whose entries variably carry a `#` is written `pipe: [{strip_prefix: "#"}, trim]`, and if
absence emptied the value then every tag without one would disappear and the crawl would still
succeed.

`split` is neither: with no separator present it MUST yield a one-element list, so a single author with
no comma survives.

### 6.3 Mapping over lists

A step whose input type is a scalar, applied to a list, MUST be applied element-wise.

This is why the format has no `map` construct. Element-wise application is a property of the value, not
control flow.

### 6.4 Named pipes and defaults

`pipes` defines reusable pipes by name, inheritable through `extends`. A `pipe` MAY be a name instead of
a list.

A step that is a string naming an entry in `pipes` expands to that entry's steps, recursively, before
the pipe is validated or applied; a string naming no entry is a step name. `pipes` is a mapping, so a
child redefining a name replaces the parent's entry (§5.1). An entry reaching itself at any depth MUST be
rejected at load time.

**Every field kind has a default pipe, and this is it.** A field with no `pipe` gets the one for its
kind. These are normative, because an unspecified default means the same document behaves differently
in the interpreter, the web preview and any other reader.

| Field kind                                   | Default pipe                                                |
| -------------------------------------------- | ----------------------------------------------------------- |
| `title`, `authors`, and any `ItemList` field | `[trim, collapse_spaces]`                                   |
| `tags`                                       | `[trim, collapse_spaces, drop_empty, unique]`               |
| `synopsis`                                   | `[unwrap([a, abbr, acronym, label, span, time]), paragraphs]` |
| `chapter.body`                               | `[unwrap([a, abbr, acronym, label, span, time]), paragraphs]` |
| `cover`, `url`, and any URL-valued field     | `[trim]`                                                    |

The most specific row wins, so an `ItemList` field named `url` gets `[trim]` and not the general
`ItemList` default.

The `unwrap` step is there because those six elements carry no meaning in prose. §6.1 keeps any element
that is neither a block tag nor preserved, which is right for `b` and `em` and wrong for a `span` a
theme used for layout or an `a` pointing at a translator's donation page. Flattening them to their text
is what the reader wants, and putting the list in the default rather than inside `paragraphs` keeps it
visible and lets a site that needs its links write its own pipe.

**A default that consumes a node, applied to a text value, gets `parse_html` prepended.** `synopsis`
and `chapter.body` are frequently extracted from a JSON string rather than selected from markup, and
without this rule their default would run a node step on text.

```yaml
# effective pipe: [parse_html, unwrap([...]), paragraphs]
body: { json: data.data.body }

# effective pipe: [unwrap([...]), paragraphs]
body: { css: "#content" }
```

This applies to defaults only. A pipe the spec declares is used exactly as written, and a type mismatch
in it is a validation error under §6.1.

Declaring `pipe` **replaces** the default rather than extending it. A spec wanting the default plus one
more step names the default's steps explicitly, or references a named pipe holding them.

Two things are deliberately absent. **No title casing**, because sites generally capitalise their own
titles correctly and the ones that do not are not improved by a blanket rule that mangles acronyms and
stylised names. **No advertisement stripping**, because ad selectors are site-specific and belong in the
base spec for a site family rather than in a default every source inherits.

Implementations SHOULD express any remaining unconditional normalisation, such as tag deduplication,
through these pipes rather than as a separate pass, so a source that needs to opt out can.

### 6.5 Language handling

Language-specific behaviour SHOULD arrive as abstract specs carrying pipes, not as per-source rules.
Paragraph conventions, punctuation width, annotation markup and text direction differ by writing system,
and solving them once per language is the only approach that scales.

Text direction is **derived, never declared**. An implementation MUST make the resolved language's
direction available to steps, and MUST NOT accept a direction field on a spec. Direction is a property
of the script, so a declared value can only agree with the language or contradict it.

## 7. Hooks

### 7.1 Scope

A hook is the only escape hatch. Some sites cannot be described as data: encrypted chapter bodies, a
token handshake, a request signature. Forcing those into a declarative form would produce a worse
format for everyone else.

A hook file MAY define several hooks, and each hook function MUST be named for the hook point it serves.
The point set is closed and derived (§3.9.2), so the name is already determined and no
path-plus-function syntax is needed:

```yaml
hooks: hooks/sites/wuxiaworld.com.py # binds every hook point the file defines
```

```yaml
hooks: # or name them individually
  login: hooks/sites/wuxiaworld.com.py
  chapter.body: hooks/sites/wuxiaworld.com.py
```

An implementation MUST bind a hook point to the function named for it in the referenced file, and MUST
report a reference to a file that does not define it. **The function name is the point with its
separator replaced by an underscore**, because a point is dotted and a function name cannot be:
`chapter.body` binds to `chapter_body`, and `login` binds to `login`.

Grouping by source is what lets a source needing four hooks keep its session, credential and encoding
setup in one place instead of four copies of it.

Hooks MUST still be shared rather than copied: two sources needing the same behaviour reference the same
file.

There is no second escape hatch. A spec MUST NOT be able to name an arbitrary class or module to load in
place of the interpreter.

### 7.2 Points and signatures

Each hook point has one fixed signature, declared by the interpreter. Transform-shaped points take a
value, the document in scope, and a context: `(value, doc, ctx) -> value`. Points that are not
transforms keep the shape their job requires. `check_response` inspects a response and returns a
diagnosis or nothing; `login` performs an exchange and returns nothing.

**The context is a defined object, not an opaque handle.** An implementation MUST expose at least:

| On `ctx`          | For                                                                       |
| ----------------- | ------------------------------------------------------------------------- |
| the HTTP session  | making requests, including anything the declarative fields cannot express |
| the resolved spec | reading `base_url`, `language` and anything else declared                 |
| `vars`            | the same values templates see, already evaluated and cached               |
| `state`           | a mutable, concurrency-safe mapping scoped to one crawl of one novel      |

`state` exists because hooks need to share what `vars` cannot express. A `login` hook obtains a bearer
token that three other hooks must send; a `novel.request` hook decodes a session key the chapter hook
needs. Without a defined place for it, a hook file's only option is a module-level global, which is
process-wide and therefore wrong the moment two novels are crawled at once.

**How it is stored, and how a shared hook reaches the right one.** One `Context` is created per crawl,
held by that crawl's interpreter instance, and **passed as a parameter** into every hook call from every
thread. There is no ambient lookup: an implementation MUST NOT expose it through a module global, a
thread-local, or a context variable. A hook module is shared by every crawl, so the only thing that can
tell it which crawl it is serving is its arguments.

```
crawl starts   -> one Context { session, spec, vars cache, state {} }
login          -> writes state, single-threaded
novel stage    -> writes state, single-threaded
chapter stages -> read state, many threads, same Context object
crawl ends     -> Context discarded, nothing persisted
```

That order is what makes it workable. Credentials and page-derived ids are written while one thread is
running, and the parallel chapter threads only read them.

Three limits follow:

- `state` MUST be safe for concurrent access and MUST offer an atomic update, because chapter threads
  may write to it even though the common case does not.
- A chapter stage MUST NOT rely on `state` written by **another chapter**. Those run in parallel with no
  ordering between them, so anything one chapter needs belongs in the call, in a `chapter`-scoped var,
  or in a field carried from the table of contents.
- `state` holds small values: a token, an id, a session key. It is not a content cache, it is not
  persisted, and a hook MUST NOT rely on it surviving the crawl.

**`state` is deliberately untyped.**

```python
state: MutableMapping[str, Any]
```

String keys, any value, no declared fields and no schema. `Novel`, `Chapter` and `SearchResult` are
_output_: they are serialized, stored, and compared across crawler versions, which is why they declare
fields. `state` is _scratch_: it never leaves the process and is discarded when the crawl ends. Typing
it would constrain hooks without protecting anything downstream.

It MUST also accept values that are not serializable. A hook speaking a binary protocol keeps a live
client in there, along with whatever it compiled to talk to the protocol.

**Keys MUST be namespaced by the hook file that owns them.** A spec may reference several hooks, and a
`shared/` hook may be referenced by many specs, so two independent hooks reaching for `state["token"]`
is a collision waiting to happen between files that have never seen each other. Prefixing with the
hook's file stem, as in `state["wuxiaworld.com/bearer"]`, makes ownership visible and the collision
impossible.

### 7.3 Constraints

**`hooks/` is an importable package.** A hook MAY import from `hooks/lib/` and from `hooks/shared/`,
which is how a source with several hooks keeps its session setup, credential handling and decoding in
one place. An implementation MUST make those imports resolve without the spec author manipulating paths.

**Two different naming rules follow.** A file that is imported by name MUST be importable by name, so
every filename in `hooks/lib/` and `hooks/shared/` MUST be a valid identifier in the implementation
language: no dots, no hyphens. A file in `hooks/sites/` is named for its host (§8.2) and therefore
usually is not one, `wtr-lab.com.py` having both. Those are never imported by name; an implementation
MUST load a spec's referenced hook file **by path** and MUST NOT require its name to be importable.

A hook MUST NOT import from `hooks/sites/`. Reaching into another host's implementation couples two
sources that have no relationship, and behaviour worth sharing belongs in `lib/` or `shared/` where it
can be found.

**A hook MUST be re-entrant and MUST NOT hold mutable module-level state.** Its module is imported once
and shared by every crawl of every host that references it, and it is called from several worker threads
within each of those crawls. A module-level variable is therefore neither per-crawl nor per-thread, and
a hook that caches a token or a session in one is wrong in a way that appears only under load, as one
novel receiving another's credentials.

Per-crawl values belong in `ctx.state`. Values derived from a document belong in a `vars` entry, which
the interpreter caches correctly for its scope.

A hook SHOULD be small enough to review and test in isolation. A hook MUST NOT reach into the
interpreter's internals beyond its documented context.

The hook point set is closed. If a site's behaviour cannot be reached by any point, that is evidence for
**a new hook point**, considered on its own merits, and not a reason to permit arbitrary code loading.

### 7.4 Provenance

This repository is Apache-2.0 and the crawler it replaces is GPL-3.0-or-later, held by many contributors
with no contributor licence agreement. A hook MUST NOT be copied or adapted from the crawler's existing
sources.

A site's own scheme is not anyone's copyright. A hook MAY implement the algorithm a site uses, the byte
layout it expects, or the header it checks. Reading existing code to understand a site is fine;
reproducing its expression is not.

## 8. Repository layout

### 8.1 Host normalisation

A host is derived from a URL by one function, used identically everywhere: lowercase, discard the
scheme, discard any userinfo, discard the port, discard a trailing slash, discard a leading `www.`, and
encode to A-label form using IDNA2008 [RFC5890] [RFC5891].

The IDNA version is named because IDNA2003 and IDNA2008 disagree on real inputs, `ß` and `ς` among them.
Two implementations picking different ones would derive different filenames for the same host, and the
entire layout rests on that derivation being identical everywhere.

Scheme and `www.` variants MUST never be separate specs. One document answers for all of them.

### 8.2 Paths

| Folder                  | Contents                                                                       |
| ----------------------- | ------------------------------------------------------------------------------ |
| `specs/<host>.yaml`     | Live concrete specs, one per host.                                             |
| `disabled/<host>.yaml`  | Concrete specs that are not served.                                            |
| `base/<name>.yaml`      | Abstract specs.                                                                |
| `hooks/shared/`         | Hook functions serving more than one host.                                     |
| `hooks/sites/<host>.py` | Hook functions for one host, named as its spec is.                             |
| `hooks/lib/`            | Helper modules. Imported by hooks, never referenced by a spec.                  |
| `fixtures/<host>/`      | Recorded documents for offline testing. A convention; its layout is not part of this contract. |
| `schema/`               | The generated JSON Schema.                                                     |

The path rule is the host and nothing else. No sharding function, no language directories, no lookup. It
MUST be computable identically in every implementation.

Language is deliberately not part of the path. A host's language belongs in its spec, and a directory
cannot describe a host that serves several.

### 8.3 Invariants

CI MUST enforce all of these:

1. Every `specs/` and `disabled/` filename equals the normalised host of its `base_url`.
2. `disabled/` documents declare a non-empty `disabled` reason; `specs/` documents declare none.
3. No host appears in more than one folder.
4. `base/` documents declare no `base_url`.
5. Every `extends` path exists and names a document in `specs/` or `base/`.
6. Every `hooks` path exists and names a file in `hooks/`.
7. Every file in `hooks/shared/` and `hooks/sites/` is either referenced by a spec or imported by a file
   that is. Files in `hooks/lib/` are exempt, since they exist to be imported.
8. `can_search` and `can_login` are never `true` without the capability resolving.
9. A regenerated JSON Schema matches the committed one.

Invariants 1 through 7 are checkable without an interpreter, and SHOULD be, so that layout regressions
are caught even when the interpreter cannot be installed.

### 8.4 Disabling

Disabling is a move, not a deletion. A spec for a site that has gone down or begun blocking moves to
`disabled/` and gains a reason. The work and the reason stay together, and restoring it is a move back.

A site that has been **redesigned** is a parser fix, not a disabled source. Losing that distinction is
how a working site ends up unsupported.

A host that never had an implementation is also a `disabled/` document, carrying `base_url` and
`disabled` and nothing else. The requirement in §3.3 permits this precisely because it is disabled.

An interpreter MUST surface a disabled host as known-but-unavailable, with its reason, rather than as
unknown.

## 9. Distribution

### 9.1 The manifest

Clients poll a manifest to learn what changed. It carries one digest per distributable file, including
files that have no host of their own, because a spec's behaviour changes when its base or hook changes
while its own bytes do not.

```json
{
  "rev": "…",
  "specs": {
    "<host>": {
      "file": "…",
      "sha": "…",
      "spec": 1,
      "extends": "…",
      "hooks": ["…"]
    }
  },
  "disabled": {
    "<host>": { "file": "…", "sha": "…", "spec": 1, "reason": "…" }
  },
  "bases": { "<name>": { "file": "…", "sha": "…" } },
  "hooks": { "<path>": { "sha": "…" } }
}
```

`sha` is a SHA-256 [FIPS180] digest of the file's bytes, hex-encoded in lower case. Staleness is a digest
mismatch, which needs no version authority and is reproducible from a bare checkout.

`rev` changes whenever any entry does and is otherwise stable, so it can serve an HTTP entity tag.

Digests are over **raw** files, not resolved specs, because clients fetch raw files and resolve locally.
A client MUST therefore follow `extends` and `hooks` and refetch those when their digests move.

**Hooks are keyed by repository-relative path, not by name.** They live in three directories (§8.2), so
`hooks/shared/decrypt.py` and `hooks/sites/decrypt.py` are different files with the same stem, and a
bare name cannot tell them apart. A spec references a hook by path already, so the manifest key is the
same string the spec contains.

**Every file under `hooks/` MUST appear, including `hooks/lib/`.** A spec's `hooks` list names only the
files it references, and a referenced file MAY import from `hooks/lib/` and `hooks/shared/` (§7.3). A
client that fetched only the referenced files would install a hook whose imports were never downloaded,
and would fail at import time on the first crawl rather than at sync time. A client MAY fetch all of
them, and MUST fetch at least the transitive closure of what its specs reference.

The manifest is generated and published. It MUST NOT be committed.

### 9.2 Version gating

A client MUST skip any spec whose `spec` value its interpreter does not implement, and SHOULD report that
an update is available. Skipping is per spec, never per manifest, so one new feature does not freeze an
entire corpus for older clients.

### 9.3 Two-tier resolution

While a Python-based corpus still exists, specs are a tier in front of it. A host resolves to its spec if
one exists, and otherwise to the legacy source.

Precedence MUST be by tier, and MUST NOT be by version or timestamp. An implementation that compares
timestamps will let a re-downloaded legacy source outrank the spec meant to replace it, intermittently
and depending on file modification times.

A host disabled in the spec tier MUST NOT fall through to a legacy source. Disabled is an answer, not a
miss. Falling through would re-enable every host anyone deliberately turned off.

### 9.4 Version as a cache key

Implementations commonly stamp a crawler version onto stored content and compare it to decide whether
stored content is stale. Where that is so:

- The stamp MUST accommodate a digest, not only an integer.
- Comparison MUST be scoped to a tier. A host moving from a legacy source to a spec MUST NOT invalidate
  stored content, which is safe precisely because equivalence is verified before a spec is adopted.
- An absent or unrecognisable stamp MUST be treated as unknown and MUST NOT invalidate.

Ignoring this makes an entire stored library appear stale at once, and nothing about the symptom points
at a metadata change.

A digest is not a date. An interpreter or catalogue that wants to display when a source last changed MUST
carry a separate timestamp rather than parsing the version.

## 10. Security model

A spec cannot execute code. It selects, fetches, transforms and names hooks. That is what allows a spec
to be validated and test-run without a sandbox, and it is the property that lets an untrusted person
author one.

Implementations offering a hosted test runner MUST observe this boundary:

1. A submitted spec MAY reference hooks, but only hooks already present in the deployed repository. The
   runner MUST resolve hook references against installed files.
2. The runner MUST NOT accept hook source code, or any Python, from a request.
3. New or changed hooks are code and MUST go through the same review as any code.

Open authoring covers the data. Code keeps its gate.

A spec MUST NOT be able to read the local filesystem, and `RepoPath` values MUST be confined to the
repository. An implementation MUST reject a path escaping it.

Distributing hooks is distributing code, with the trust that implies. A hook is small, named, and
reviewed in isolation, and most sources carry no code at all.

## 11. References

### 11.1 Normative

| Tag          | Reference                                                                                                                         |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| [RFC2119]    | Bradner, S., "Key words for use in RFCs to Indicate Requirement Levels", BCP 14, RFC 2119, March 1997.                            |
| [RFC8174]    | Leiba, B., "Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words", BCP 14, RFC 8174, May 2017.                               |
| [RFC5890]    | Klensin, J., "Internationalized Domain Names for Applications (IDNA): Definitions and Document Framework", RFC 5890, August 2010. |
| [RFC5891]    | Klensin, J., "Internationalized Domain Names in Applications (IDNA): Protocol", RFC 5891, August 2010.                            |
| [FIPS180]    | National Institute of Standards and Technology, "Secure Hash Standard", FIPS PUB 180-4, August 2015.                              |
| [SELECTORS]  | W3C, "Selectors Level 3", W3C Recommendation, November 2018.                                                                      |
| [SELECTORS4] | W3C, "Selectors Level 4", W3C Working Draft. Cited for `:has()` and `:scope` only.                                                |
| [SOUPSIEVE]  | Bradshaw, I., "Soup Sieve", CSS selector library for Beautiful Soup. Cited for the semantics of `:-soup-contains` only.           |
| [ISO639]     | ISO 639-1, "Codes for the representation of names of languages, Part 1: Alpha-2 code".                                            |
| [JSONSCHEMA] | Wright, A., Andrews, H., Hutton, B., "JSON Schema: A Media Type for Describing JSON Documents".                                   |

### 11.2 Informative

| Tag      | Reference                                                                          |
| -------- | ---------------------------------------------------------------------------------- |
| [YAML]   | Ben-Kiki, O., Evans, C., dot NET, I., "YAML Ain't Markup Language Version 1.2".    |
| [WPREST] | WordPress, "REST API Handbook". Cited for the `X-WP-TotalPages` pagination header. |

## 12. No control flow

This format has no conditionals, no user-defined loops and no expressions. `paginate` is the only
iteration, and it is declared rather than written.

The moment a source needs a branch, it uses a hook. This is the guardrail against the standard failure
of formats like this one, which is becoming a poor programming language by increments.

Arithmetic is included in that. A page count derived by dividing a total, or a table of contents fetched
in computed index ranges, is a hook rather than a field.
