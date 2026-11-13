"""Review #66 regressions: untrusted containers cannot win promotion (REPAIR-001/002)."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro import CssRecipe, Limits, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.application.extraction import extract_document
from rastro.application.recipe_ci import evaluate_candidate
from rastro.domain.recipe_compare import RepairPolicy
from rastro.domain.repair import RepairCandidate, RepairEvidence
from rastro.mocklab.fixtures import build_fixture


class Event(BaseModel):
    title: str
    venue: str


SOURCE = Source(url="https://example.test/events")
RECIPE = CssRecipe(
    record_selector="article.dance", fields={"title": "h2.title::text", "venue": "p.venue::text"}
)
CARD = '<article class="dance"><h2 class="title">{t}</h2><p class="venue">{v}</p></article>'
BEFORE = (
    "<html><body><main>"
    + CARD.format(t="Synthetic dance", v="Synthetic hall")
    + CARD.format(t="Synthetic opera", v="Synthetic room")
    + "</main></body></html>"
)
NEW = BEFORE.replace('class="dance"', 'class="event card"')


def extract(html, recipe=RECIPE):
    schema = Product if "name" in recipe.fields else Event
    return extract_document(
        html, source=SOURCE, schema=schema, recipe=recipe, parser=ScraplingParser(), limits=Limits()
    )


def propose(html, before=BEFORE, recipe=RECIPE):
    result = extract(before, recipe)
    snapshot = ScraplingRelocation().capture(
        before, source=SOURCE, recipe=recipe, result=result, limits=Limits()
    )
    candidates = ScraplingRelocation().relocate(html, recipe, snapshot, Limits())
    fixture = build_fixture(
        before, source=SOURCE, schema=Event, recipe=recipe, result=result, synthetic_count=0
    )
    return candidates, fixture


def verdict(candidate, html, fixture):
    return evaluate_candidate(
        candidate,
        html,
        source=SOURCE,
        schema=Event,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=datetime(2026, 11, 8, tzinfo=UTC),
        fixtures=(fixture,),
        policy=RepairPolicy(auto_promote=True),
    ).decision


@pytest.mark.parametrize(
    "html",
    [
        NEW.replace(
            "</main>",
            '<article class="event card promo"><img src="x.png"><h2 class="title">50% OFF</h2>'
            '<p class="venue">Buy now</p><a>Buy</a><button>x</button></article></main>',
        ),
        NEW.replace(
            "</body>",
            '<footer><article class="card"><h2 class="title">Newsletter</h2>'
            '<p class="venue">Subscribe</p><a>go</a></article></footer></body>',
        ),
        NEW.replace(' class="event card"', "").replace(
            "</main>",
            '<aside><div><article><h2 class="title">Sponsored</h2><p class="venue">Ad</p>'
            '<img src="a.png"></article></div></aside></main>',
        ),
        (
            '<html><body><main><article class="news"><h2 class="title">Site maintenance tonight'
            '</h2><p class="venue">Our box office is closed</p></article></main></body></html>'
        ),
        (
            '<html><body><main><article class="news"><h2 class="headline">Site maintenance tonight'
            '</h2><p class="summary">Our box office is closed</p></article></main></body></html>'
        ),
        (
            '<html><body><main><section><article class="notice"><h2>No upcoming events</h2>'
            "<p>Check back soon</p></article></section></main></body></html>"
        ),
    ],
    ids=["promo", "footer", "classless-nested", "news-same-fields", "news-new-fields", "notice"],
)
def test_reviewer_article_traps_never_promote(html):
    """REPAIR-002: every p_relocation.py trap must be held, even with valid strings."""
    candidates, fixture = propose(html)
    assert all(verdict(c, html, fixture) in {"quarantine", "reject"} for c in candidates)


@pytest.mark.parametrize(
    "extra",
    [
        (
            '<li class="show destacado"><img><h3>Abono anual</h3>'
            '<span class="fecha">2099-03-20</span><a>Comprar</a><em>-20%</em></li>'
        ),
        (
            '<footer><ul><li class="show"><h3>Boletín</h3>'
            '<span class="fecha">2099-03-20</span><a>Subscribe</a></li></ul></footer>'
        ),
    ],
    ids=["promo", "footer"],
)
def test_reviewer_list_traps_never_promote(extra):
    """REPAIR-002: verify_all H1 must quarantine valid but unrelated list rows."""
    before = (
        "<html><body><main><ul>"
        + "".join(
            f'<li class="evento"><h3>Dance {i}</h3>'
            f'<span class="fecha">2099-03-{10 + i}</span><a>Ver</a></li>'
            for i in range(5)
        )
        + "</ul></main></body></html>"
    )
    html = before.replace('class="evento"', 'class="show"')
    target = "</body>" if extra.startswith("<footer>") else "</ul>"
    html = html.replace(target, extra + target, 1)
    recipe = CssRecipe(
        record_selector="main > ul > li.evento",
        fields={"title": "h3::text", "venue": "span.fecha::text"},
    )
    candidates, fixture = propose(html, before, recipe)
    assert all(verdict(c, html, fixture) in {"quarantine", "reject"} for c in candidates)


def test_unrelated_class_hash_cannot_break_a_container_tie():
    """SCR-002: verify_all H2 must not prefer banner because its digest looks similar."""
    html = (
        "<html><main>"
        '<article class="card"><h2 class="title">Real dance</h2>'
        '<p class="venue">Real hall</p></article>'
        '<article class="banner"><h2 class="title">Sponsor ad</h2>'
        '<p class="venue">Click here</p></article>'
        "</main></html>"
    )
    candidates, fixture = propose(html)
    assert all("banner" not in c.recipe.record_selector for c in candidates)
    assert all(verdict(c, html, fixture) in {"quarantine", "reject"} for c in candidates)


def test_excessive_class_tokens_fail_closed():
    """SCR-002: class-token hashing enforces a 100-token per-element budget."""
    from rastro import ResourceLimitError

    classes = " ".join(f"c{i}" for i in range(101))
    html = (
        f'<html><main><article class="{classes}">'
        '<h2 class="title">Dance</h2><p class="venue">Hall</p></article></main></html>'
    )
    with pytest.raises(ResourceLimitError, match="100 class tokens"):
        propose(html)


def test_record_selector_evidence_is_ambiguous_by_default():
    """REPAIR-002: an unqualified container tie cannot bypass the domain CI gate."""
    candidate = RepairCandidate(
        recipe=RECIPE,
        origin="scrapling_relocation",
        rationale="Tied containers",
        evidence=(RepairEvidence(field="record_selector", match_count=2),),
    )
    assert candidate.ambiguous


class Product(BaseModel):
    name: str
    price: str
    stock: str | None = None


PRODUCT_RECIPE = CssRecipe(
    record_selector="div.product",
    fields={"name": "h3.name::text", "price": "span.price::text", "stock": "span.stock::text"},
)


def _catalogue(css_class, missing_stock=None):
    cards = []
    for i in range(5):  # one gap in five keeps an optional field at 0.8
        stock = "" if i == missing_stock else f'<span class="stock">{i} left</span>'
        cards.append(
            f'<div class="{css_class}"><h3 class="name">Lamp {i}</h3>'
            f'<span class="price">{10 + i}.00</span>{stock}</div>'
        )
    return "<html><body><main>" + "".join(cards) + "</main></body></html>"


def _product_verdicts(html, before=None):
    before = before or _catalogue("product")
    result = extract_document(
        before,
        source=SOURCE,
        schema=Product,
        recipe=PRODUCT_RECIPE,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    snapshot = ScraplingRelocation().capture(
        before, source=SOURCE, recipe=PRODUCT_RECIPE, result=result, limits=Limits()
    )
    candidates = ScraplingRelocation().relocate(html, PRODUCT_RECIPE, snapshot, Limits())
    fixture = build_fixture(
        before,
        source=SOURCE,
        schema=Product,
        recipe=PRODUCT_RECIPE,
        result=result,
        synthetic_count=0,
    )
    return [
        evaluate_candidate(
            c,
            html,
            source=SOURCE,
            schema=Product,
            parser=ScraplingParser(),
            limits=Limits(),
            observed_at=datetime(2026, 11, 8, tzinfo=UTC),
            fixtures=(fixture,),
            policy=RepairPolicy(auto_promote=True),
        ).decision
        for c in candidates
    ]


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_a_record_without_an_optional_child_still_relocates(missing):
    """REPAIR-001, SCR-002: absent optional children do not make rows heterogeneous."""
    assert _product_verdicts(_catalogue("item-card", missing)) == ["promote"]


def test_optional_child_missing_before_and_after_relocates():
    """REPAIR-001, SCR-002: the snapshot itself may come from a row set with gaps."""
    before = _catalogue("product", 1)
    assert _product_verdicts(_catalogue("item-card", 2), before) == ["promote"]


def test_a_promo_decoy_beside_rows_missing_optional_children_is_refused():
    """REPAIR-002: the optional-child tolerance must not admit a decoy container."""
    html = _catalogue("item-card", 1).replace(
        "</main>",
        '<article class="promo"><h3 class="name">50% OFF</h3>'
        '<span class="price">0.00</span><a>Buy</a></article></main>',
    )
    before = _catalogue("product")
    result = extract_document(
        before,
        source=SOURCE,
        schema=Product,
        recipe=PRODUCT_RECIPE,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    snapshot = ScraplingRelocation().capture(
        before, source=SOURCE, recipe=PRODUCT_RECIPE, result=result, limits=Limits()
    )
    candidates = ScraplingRelocation().relocate(html, PRODUCT_RECIPE, snapshot, Limits())
    for candidate in candidates:
        names = [r.data.name for r in extract(html, candidate.recipe).records]
        assert "50% OFF" not in names


def test_a_same_class_row_with_extra_children_stays_ambiguous():
    """REPAIR-002: tolerance covers omitted children only, never extra ones."""
    html = _catalogue("item-card", 1).replace(
        "</main>",
        '<div class="item-card"><img src="ad.png"><h3 class="name">Sponsored</h3>'
        '<span class="price">0.00</span><a>Buy</a></div></main>',
    )
    assert all(v in {"quarantine", "reject"} for v in _product_verdicts(html))


class Stocked(BaseModel):
    name: str
    price: str
    stock: str | None = None


STOCKED_RECIPE = CssRecipe(
    record_selector="div.product",
    fields={"name": "h2.name::text", "stock": "span.stock::text", "price": "span.price::text"},
)


def _stocked(card_class, stock_class, missing=None):
    cards = "".join(
        f'<div class="{card_class}"><h2 class="name">Lamp {i}</h2>'
        + ("" if i == missing else f'<span class="{stock_class}">{i} left</span>')
        + f'<span class="price">{10 + i}.00</span></div>'
        for i in range(4)
    )
    return f"<html><body><main>{cards}</main></body></html>"


@pytest.mark.parametrize("missing", [0, 2])
def test_a_relocated_field_never_reads_a_siblings_value_in_a_thinned_record(missing):
    """REPAIR-002, SCR-002: relocated selectors are tag+class, not positional."""
    before = _stocked("product", "stock")
    result = extract_document(
        before,
        source=SOURCE,
        schema=Stocked,
        recipe=STOCKED_RECIPE,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    snapshot = ScraplingRelocation().capture(
        before, source=SOURCE, recipe=STOCKED_RECIPE, result=result, limits=Limits()
    )
    html = _stocked("product", "qty", missing)
    candidates = ScraplingRelocation().relocate(html, STOCKED_RECIPE, snapshot, Limits())
    for candidate in candidates:
        assert "nth-child" not in candidate.recipe.fields["stock"]
        rows = extract_document(
            html,
            source=SOURCE,
            schema=Stocked,
            recipe=candidate.recipe,
            parser=ScraplingParser(),
            limits=Limits(),
        ).records
        assert all(r.data.stock != r.data.price for r in rows)
        assert all((r.data.stock is None) == (i == missing) for i, r in enumerate(rows))


def test_omissions_must_match_tag_and_class_and_empty_rows_are_heterogeneous():
    """REPAIR-002: a row is a thinned template row only if its children keep tag and class."""
    from scrapling.parser import Selector

    from rastro.adapters.scrapling_container import homogeneous

    def rows(html):
        page = Selector(html, adaptive=False)
        return page, list(page.css("div.card"))

    full = (
        '<div class="card"><h2 class="name">a</h2><span class="stock">1</span>'
        '<span class="price">2</span></div>'
    )
    cases = {
        '<div class="card"><h2 class="name">a</h2><span class="price">2</span></div>': True,
        '<div class="card"><h2 class="name">a</h2><span class="promo">2</span></div>': False,
        '<div class="card"></div>': False,
        '<div class="card"><span class="price">2</span></div>': False,
    }
    for other, expected in cases.items():
        page, found = rows(f"<main>{full}{full}{other}</main>")
        assert homogeneous(page, found, found) is expected, other


def test_text_only_records_with_identical_shapes_stay_homogeneous():
    """REPAIR-002: records without element children (table cells) can still relocate."""
    from scrapling.parser import Selector

    from rastro.adapters.scrapling_container import homogeneous

    page = Selector(
        '<table><tr><td class="cell">1</td></tr><tr><td class="cell">2</td></tr></table>',
        adaptive=False,
    )
    found = list(page.css("td.cell"))
    assert homogeneous(page, found, found) is True
