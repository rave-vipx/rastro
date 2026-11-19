# Crawling linked pages

A job can discover pages from its starting URLs with a `[spider]` table:

```toml
[spider]
mode = "spider"
links = ["a.product::attr(href)", "a.next::attr(href)"]
allow = ["/products"]
deny_pattern = '^/products/archive/'
max_depth = 2
max_pages = 25
```

Each expression must read a URL attribute, normally `::attr(href)`; existing
`::attr(name)` expressions such as `::attr(data-href)` remain supported. A single
string is also accepted.
A list holds up to eight expressions of at most 1024 characters each. Expressions
are evaluated in list order, with matches in document order within each expression;
duplicate destinations are followed once. Every expression passes the same selector
cost checks as a recipe.

Do not write `links = "a.product, a.next::attr(href)"`: the attribute suffix does
not apply safely to both selector groups. This produces `link_group_refused` with
a hint to use a list. Migrate it to
`links = ["a.product::attr(href)", "a.next::attr(href)"]`.
Commas inside quoted attribute values are allowed. To discover URLs from extracted
records instead, set `link_field` to a declared field and omit `links`.

## Filter before the page limit

`allow` and `deny` match whole path prefixes. `allow_pattern` and `deny_pattern`
match the normalized, percent-decoded path with a linear-time regular expression;
queries do not participate. Patterns may have up to 512 characters and cannot use
look-around or backreferences. Anchor with `^` and `$` when the whole path must match.
A deny match takes precedence over an allow match.

Filtering happens before spending a page slot, so a rejected link cannot crowd out
an allowed page at `max_pages`. Rejected paths appear as `path_denied` in the crawl
report. The starting page still consumes a page slot. With `seed = "sitemap"`, path
filters apply to discovered pages, not to child sitemap URLs. `seed_order` controls
sitemap entry order; it accepts `document`, `reverse` or `lastmod`.

All followed pages use the existing acquisition policy, destination checks and
request budgets. Link discovery grants no network access by itself.

## Page identity and evidence

Crawling and recipe pagination use a canonical URL to recognize repeated pages.
It retains scheme, host, port and path, sorts query parameters, and removes:

- URL credentials, path session parameters and fragments;
- tracking keys such as `utm_*`, `fbclid`, `gclid` and `mc_eid`;
- secret-like query keys, including `token`, `key`, `sig`, `signature`, `session`,
  `sid`, `auth`, `password`, `code` and `state`. Keys are split into words on
  separators and camelCase, so `access_token`, `accessToken`, `X-Amz-Signature` and
  `jsessionid` are removed while `keyword` or `author` are kept.

For example, `https://catalogue.example/products?utm_source=mail&page=2&token=hidden`
has the identity `https://catalogue.example/products?page=2`. Page 3 remains a separate
page. Duplicate query keys and blank values are retained when their keys are allowed.
Requests still use the original URL: canonicalization changes identity and evidence,
not the address sent to the acquisition adapter.

Field evidence uses `page_url` for the canonical acquired page when it differs from
its query-free source URL. Redirected pages use the final acquired URL. `source_url`
continues to identify the starting source when pages are combined. Logs, hooks and
crawl reports carry no URL queries.

The key filter is conservative: secret-like key names may be removed even when their
values are ordinary data. It cannot recognize a secret placed under an unrelated key
such as `page`; treat retained page parameters and paths as source data subject to
your storage and retention policy.
