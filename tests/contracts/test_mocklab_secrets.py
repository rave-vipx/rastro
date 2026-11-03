"""MOCK-005: a secret planted in every category never reaches any written fixture file."""

from decimal import Decimal

import pytest
from pydantic import BaseModel, Field

from rastro import Rastro, Recipe, Source
from rastro.mocklab import build_fixture, load_fixture, verify_fixture

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwbGFudGVkIn0.c2lnbmF0dXJlLXBsYW50ZWQ"
SECRETS = {
    "cookie_header": "sid=cookie-header-planted-7781",
    "authorization_header": "Bearer authz-header-planted-4410",
    "jwt": JWT,
    "bearer_text": "bearer-text-planted-9921",
    "api_key": "sk_live_PLANTEDapikey0000000000",
    "email": "planted.person@example.org",
    "phone": "+1 415 555 0199",
    "password_input": "hunter2-password-planted",
    "hidden_input": "csrf-hidden-planted-5531",
    "script": "script-secret-planted-6620",
    "inline_cookie": "cookie-inline-planted-1177",
    "form_text": "form-value-planted-3392",
    "textarea": "textarea-planted-8875",
    "query_secret": "query-token-planted-2240",
    "fragment": "fragment-planted-6603",
    "url_credentials": "urlpass-planted-0042",
    "path_token": "PathTokenPlanted0123456789abcdef",
    "comment": "comment-planted-5157",
    "confidential_text": "Confidential merger codename Bluebird",
    "meta": "meta-content-planted-7319",
    "title_attr": "title-attr-planted-4402",
    "data_attr": "data-attr-planted-9013",
    "style": "style-planted-2276",
    "event_handler": "onclick-planted-3381",
    "jsonld_unextracted": "jsonld-description-planted-1840",
}

PAGE = f"""<!doctype html>
<html><head>
<title>{SECRETS["confidential_text"]}</title>
<meta name="csrf" content="{SECRETS["meta"]}">
<script>var token = "{SECRETS["script"]}"; document.cookie = "{SECRETS["inline_cookie"]}";</script>
<style>.x {{ background: url("{SECRETS["style"]}") }}</style>
<script type="application/ld+json">
{{"@type": "Organization", "description": "{SECRETS["jsonld_unextracted"]}"}}
</script>
<!-- {SECRETS["comment"]} -->
</head><body>
<p>Contact {SECRETS["email"]} or {SECRETS["phone"]}.
Authorization: Bearer {SECRETS["bearer_text"]}</p>
<p data-session="{JWT}" title="{SECRETS["title_attr"]}" data-x="{SECRETS["data_attr"]}"
   style="color: {SECRETS["style"]}" onclick="send('{SECRETS["event_handler"]}')">
key {SECRETS["api_key"]}</p>
<form action="https://user:{SECRETS["url_credentials"]}@shop.example.com/login?token={SECRETS["query_secret"]}">
  <input type="password" name="pw" value="{SECRETS["password_input"]}">
  <input type="hidden" name="csrf" value="{SECRETS["hidden_input"]}">
  <input type="text" name="note" value="{SECRETS["form_text"]}">
  <textarea name="msg">{SECRETS["textarea"]}</textarea>
</form>
<a href="/reset/{SECRETS["path_token"]}#{SECRETS["fragment"]}">reset</a>
<article class="product"><h2>Notebook</h2><span data-price="12.50"></span></article>
<article class="product"><h2>Pen</h2><span data-price="2.00"></span></article>
</body></html>
"""

HEADERS = {
    "Set-Cookie": SECRETS["cookie_header"],
    "Authorization": SECRETS["authorization_header"],
    "X-Api-Key": SECRETS["api_key"],
    "Content-Type": "text/html; charset=utf-8",
}


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0)


RECIPE = Recipe(
    strategy="css",
    record_selector="article.product",
    fields={"name": "h2::text", "price": "span::attr(data-price)"},
)


@pytest.fixture
def written(tmp_path):
    source = Source(url=f"https://shop.example.com/p?token={SECRETS['query_secret']}")
    result = Rastro().extract_html(PAGE, source=source, schema=Product, recipe=RECIPE)
    bundle = build_fixture(
        PAGE, source=source, schema=Product, recipe=RECIPE, result=result, headers=HEADERS
    )
    bundle.write(tmp_path)
    return tmp_path


@pytest.mark.parametrize("category", sorted(SECRETS))
def test_planted_secret_never_reaches_a_written_file(written, category):
    secret = SECRETS[category]
    files = sorted(written.iterdir())
    assert {path.name for path in files} == {
        "body.html",
        "expected.json",
        "manifest.json",
        "records.json",
    }
    for path in files:
        assert secret not in path.read_text(), f"{category} leaked into {path.name}"


def test_sanitized_fixture_still_replays_and_keeps_allowlisted_headers(written):
    bundle = load_fixture(written)
    assert bundle.manifest.response_headers == {"content-type": "text/html; charset=utf-8"}
    assert bundle.manifest.source.url == "https://shop.example.com/p"
    replayed = verify_fixture(bundle, Product)
    assert [record.data.name for record in replayed.records] == ["Notebook", "Pen"]
    body = (written / "body.html").read_text()
    assert '<input type="' in body and "<!--" in body.split("\n", 1)[0]
    assert body.count("<!--") == 1
