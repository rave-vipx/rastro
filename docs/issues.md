# Issue codes

Every result carries `issues`. Each issue has a `code`, and sometimes a `record_index`, a `field` and a
`message`. A `message` is a fixed token or a built-in validation type. It never holds a value from the page.

"Saved" says whether records from that run reach your output. Codes in more than one table row are
grouped by theme. A test fails when a code in `src/rastro` has no row here.

## Records and extraction

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `validation_error` | A field value does not match your schema. `field` and `message` say which. | The bad record is rejected. Others are saved. | Fix the schema or the recipe for that field. |
| `missing_fields` | A record lacks required fields. | The record is rejected. | Check the selectors. The page may have changed. |
| `missing_evidence` | A value has no proof of where it came from. | The record is rejected. | Fix the selector so it points at real page content. |
| `ambiguous_match` | A selector matched more than one thing for a single field. | The record is rejected. | Make the selector more specific. |
| `invalid_record` | One structured record is not an object. | That record is skipped. | Check the source data. |
| `invalid_text` | A record holds text that cannot be stored as UTF-8. Lone surrogates in JSON and JSON-LD are already replaced with U+FFFD when decoded. | The record is rejected. Others are saved. | Check the source data or a custom validator that builds text. |
| `invalid_json` | The response is not valid JSON. | No. | Check the URL and the content type. |
| `embedded_json_not_found` | An `embedded_json` recipe's `source` selector matched no node, or the node lacks the attribute. | No. | Fix `source`. The page may have moved the data. |
| `repaired_json` | A JSON or JSON-LD block from the page parsed only after Rastro repaired it, for example by escaping raw control characters inside strings. Values and provenance are preserved; the run reports partial success so the warning stays visible. | Yes, if the records validate. | Ask the source owner to publish valid JSON. |
| `invalid_jsonld` | A JSON-LD block could not be parsed. | The block is ignored. | Use another strategy or fix the page data. |
| `record_path_not_found` | The recipe's `record_path` is not in the JSON. | No. | Fix `record_path`. The API may have changed. |
| `invalid_record_path` | The `record_path` points at something that is not a list of records. | No. | Fix `record_path`. |
| `extraction_limit` | The page is larger than the parser limits allow. | No. | Raise the limits or pick a smaller page. |
| `not_modified` | The server says the page did not change. | Nothing new. | Nothing. This is normal. |
| `invalid_acquisition_result` | The fetch returned no usable body. | No. | Retry. Check the source. |
| `field_nulled` | An optional field had no usable value, so it was stored as null. `field` names it; the run report counts these per field. | Yes, with the field empty. | Nothing, if the field is often empty. Otherwise check its selector. |
| `null_placeholder` | A field value matched one of the field's `null_values` (such as `N/A`) and was treated as missing. | Yes, if the field is optional. | Nothing. Add more placeholders to `null_values` if needed. |
| `list_item_dropped` | One item of a list field failed validation and was left out. `field` names the list. | Yes, without that item. | Check the item selector or the item type. |
| `member_nulled` | An optional member of an object field failed validation and was stored as null. `field` names the member. | Yes, with the member empty. | Check the member's selector or type. |
| `naive_datetime_localized` | A date-time without an offset was read in the source's `timezone`. | Yes. | Set the source `timezone` if the result is off by hours. |

## Fetching and policy

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `robots_denied` | `robots.txt` forbids this URL for Rastro. | No. | Respect it, or ask the site owner. |
| `invalid_robots` | The `robots.txt` file could not be parsed. Rastro fails closed. | No. | Ask the site owner to fix it. |
| `unsupported_robots_rule` | `robots.txt` uses a rule Rastro does not support. | No. | Ask the site owner. |
| `robots_limit` | The `robots.txt` file is too large. | No. | Ask the site owner. |
| `robots_redirect_limit` | `robots.txt` redirected too many times. | No. | Check the site. |
| `destination_denied` | The address is private or not allowed. | No. | Use a public address. |
| `destination_mismatch` | The connection went to a different address than the checked one. | No. | Retry. Report it if it repeats. |
| `host_not_allowed` | The host is not in your allowed list. | No. | Add it to the policy if intended. |
| `https_required` | The policy needs HTTPS. | No. | Use an HTTPS URL. |
| `https_downgrade` | A redirect or next link went from HTTPS to HTTP. | No. | Use HTTPS links. |
| `port_not_allowed` | The port is not allowed. | No. | Use a standard port or change the policy. |
| `invalid_url` | The URL is malformed or has credentials in it. | No. | Fix the URL. |
| `invalid_redirect` | A redirect target is not valid. | No. | Check the site. |
| `redirect_limit` | Too many redirects. | No. | Check the site. |
| `access_restricted` | The server answered 401, 403 or similar. | No. | Check access rights. Do not try to bypass. |
| `host_access_restricted` | An earlier answer from this host blocked further requests. | No. | Wait, or review your access. |
| `rate_limited` | The server answered 429. | No. | Wait and retry with a slower rate. |
| `retry_after_pending` | The server asked you to wait. The wait is not over. | No. | Retry later. |
| `request_limit` | The request budget for the run is spent. | Partial. | Raise the budget or reduce the work. |
| `http_transient_failure` | A temporary server error (5xx). | No. | Retry later. |
| `http_permanent_failure` | A permanent client error (404 and similar). | No. | Fix the URL. |
| `unsupported_content_type` | The content type is not supported. | No. | Use HTML, JSON or text. |
| `invalid_encoding` | The page text could not be decoded. | No. | Check the page. |
| `body_too_large` | The response is bigger than the size limit. | No. | Raise the limit or pick a smaller page. |
| `response_too_large` | A browser response is bigger than the size limit. | No. | Raise the limit. |
| `empty_document` | The page came back empty. | No. | Retry. Check the source. |
| `invalid_locale` | The locale is not valid. | No. | Fix the locale. |
| `invalid_proxy_route` | The proxy setting is not valid. | No. | Fix the egress settings. |
| `proxy_failure` | The proxy could not connect. | No. | Check the proxy. |
| `unsupported_egress` | The egress type is not supported here. | No. | Use a supported one. |
| `egress_credentials_unavailable` | Proxy credentials were not found. | No. | Set the environment variable. |
| `auth_unavailable` | The auth secret was not found. | No. | Set the environment variable named in the job. |
| `auth_requires_https` | Auth headers are only sent over HTTPS. | No. | Use HTTPS. |
| `tls_verification_failed` | The TLS certificate is not valid. | No. | Fix the site or the URL. |
| `dns_failure` | The host name did not resolve. | No. | Check the host name. |
| `network_failure` | The network connection failed. | No. | Retry later. |
| `request_timeout` | The request took too long. | No. | Retry or raise the timeout. |
| `operation_timeout` | The whole operation took too long. | No. | Retry or raise the timeout. |
| `blocked` | The site refused automated access (for example a login wall, a captcha or a refused cloud address). The run report says which kind and links to a guide. | No. | Follow the linked guide: change egress, render mode or the site permission you have. |
| `capability_refused` | The job asked for a capability that was not granted for where it came from (a bundle, a registry recipe, an agent tool or model output). | No. | Grant it with `--allow-untrusted ORIGIN:CAPABILITY`, or `--allow-capability` for grant-only capabilities. |
| `proxy_rotated` | A request moved to the next proxy after the previous one failed or was refused. | Yes. | Nothing. Check the proxy if it repeats. |
| `ladder_exhausted` | Every acquisition level the job allows was tried and none returned the page. | No. | Allow a stronger level, or check the site in a browser. |

## Browser and scroll

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `browser_guard_failed` | The browser safety guard could not start. | No. | Reinstall the browser extra. |
| `browser_navigation_failed` | The browser could not load the page. | No. | Retry. Check the URL. |
| `challenge_unsolved` | The page showed a challenge. Rastro does not solve it. | No. | Do not bypass. Ask the site owner. |
| `scroll_without_page` | A scroll step ran with no open page. | No. | Report it as a bug. |
| `scroll_unsupported` | This browser mode cannot scroll. | No. | Use the browser mode that supports it. |
| `scroll_navigated` | The page moved to another document while scrolling. | The scroll stops. | Check the page. |
| `scroll_ignored_http` | `scroll` is set but the source is fetched without a browser. | The first page only. | Set a browser mode, or remove `scroll`. |
| `scroll_unsupported_stealth` | `scroll` is set but stealth mode cannot scroll. | The first page only. | Use browser mode, or remove `scroll`. |
| `scroll_max_steps` | The scroll reached its step cap. | Partial. | Raise `max_steps` if you need more. |
| `scroll_budget` | The request budget ended the scroll. | Partial. | Raise the budget. |
| `scroll_step_failed` | A scroll step failed. | Partial. | Retry. Check the page. |
| `scroll_records_rejected` | Scrolled records failed validation. | Partial. | Check the schema. |
| `scroll_record_limit` | The record cap was reached. | Partial. | Raise `max_records`. |
| `step_failed` | A page step (click, type, select, scroll, wait or follow) failed. | Records from before the step, as partial. | Check the step and the page. |
| `step_selector_missing` | A page step's selector matched nothing. | Records from before the step, as partial. | Fix the step's selector. The page may have changed. |
| `capture_empty` | Network capture was on but no response matched its patterns. | Records from the page, if any. | Check the `capture_network` patterns. |

## Pagination and spider

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `pagination_max_pages` | The page cap was reached. | Partial. | Raise `max_pages`. |
| `pagination_budget` | The request budget ended pagination. | Partial. | Raise the budget. |
| `pagination_invalid_next` | The next link is not valid. | Partial. | Check the next selector. |
| `pagination_off_host` | The next link leads to another host. | Partial. | Stay on one host. |
| `pagination_https_downgrade` | The next link uses HTTP. | Partial. | Use HTTPS links. |
| `pagination_page_failure` | A later page failed. | Partial. | See the page's own issue. |
| `pagination_page_failed` | A later page failed to fetch. | Partial. | See the page's own issue. |
| `pagination_record_limit` | The record cap was reached. | Partial. | Raise `max_records`. |
| `spider_budget` | The request budget ended the crawl. | Partial. | Raise the budget. |
| `spider_page_limit` | The page cap was reached. | Partial. | Raise the page limit. |
| `spider_depth_limit` | The depth cap was reached. | Partial. | Raise the depth limit. |
| `spider_page_failed` | A crawled page failed. | Partial. | See the page's own issue. |
| `sitemap_invalid` | The sitemap is not valid XML, declares a DOCTYPE or entities, or its root is not `urlset` or `sitemapindex`. | No. | Check the sitemap URL. Rastro never expands entities. |
| `sitemap_too_large` | The sitemap lists more than 50,000 URLs, or its gzip inflates past the byte cap. | No. | Use a smaller child sitemap as the source. |
| `sitemap_empty` | The sitemap lists no URLs. | No. | Check the sitemap URL or the site. |
| `sitemap_nested_index` | A child of a sitemap index is itself an index; only one level is followed. | Partial. | Use the inner sitemap as the source. |
| `sitemap_unsupported` | The sitemap seed needs plain HTTP acquisition. | No. | Use `render = "http"` for a sitemap job. |
| `spider_links_failed` | Links could not be read from a page. | Partial. | Check the link selector. |
| `spider_needs_recipe` | A spider needs a saved recipe. | No. | Create a recipe first. |
| `link_group_refused` | A group of links found on a page was not followed because it is outside the crawl's allowed hosts or patterns. | Yes. | Widen `allow_pattern`, or ignore it if intended. |
| `crawl_resumed` | A crawl continued from a saved checkpoint instead of starting again. | Yes. | Nothing. Delete the checkpoint to start fresh. |
| `crawl_time_budget` | The crawl stopped because its time budget ran out; its checkpoint is kept. | Partial. | Run again to resume, or raise the time budget. |

## Storage, memory and repair

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `storage_error` | The dataset store failed. | No. | Check the file and disk space. |
| `drift_storage_error` | Drift history could not be stored. | Yes. | Check the file and disk space. |
| `evidence_retention_failed` | Repair evidence was not kept. For `message = sensitive_data:<category>`, `field` names the field and the category (such as `phone`) the kind of data. | Yes. | If it is sensitive data, remove it from that field. Otherwise check the disk. |
| `relocation_retention_skipped` | The relocation snapshot was not kept. | Yes. | Nothing needed. Repair has less to compare. |
| `model_unavailable` | The model could not be reached. | No change. | Check the model service. |
| `model_timeout` | The model took too long. | No change. | Retry. |
| `model_response_limit` | The model answer was too large. | No change. | Retry or use a smaller page. |
| `invalid_model_response` | The model answer was not usable. | No change. | Retry. |
| `invalid_model_candidate` | The model proposed a recipe that is not valid. | No change. | Retry. |
| `model_context_limit` | The page does not fit the model context. | No change. | Use a smaller page. |
| `model_budget_exhausted` | The model call budget is spent. | No change. | Raise the budget. |
| `no_valid_recipe` | No candidate recipe passed the checks. | No change. | Fix the recipe by hand. |
| `serving_recipe_extracts_no_records` | The current recipe finds no records. | No change. | Review the page and the recipe. |
| `serving_recipe_fails_gates` | The current recipe fails the quality checks. | No change. | Review the recipe. |
| `notification_failed` | An alert could not be delivered to its channel. | Yes. | Check the channel settings and the network. |
| `hub_candidate` | A recipe from the hub was installed as a candidate and is not serving yet. | No change. | Review it, then promote it. |

## Security flags

| Code | Meaning | Saved | Next step |
| --- | --- | --- | --- |
| `instruction_like_content` | A field value looks like instructions for an AI (a prompt-injection pattern). As a flag it never holds the matched text; as a repair reason it means automatic promotion was refused and the candidate stays quarantined. | Yes, unchanged. | Treat the value as data. To activate a held candidate, review the values, then `rastro recipe promote ... --reason "..." --override-gates`. |
