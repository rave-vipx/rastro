"""Recipe memory and offline self-repair through rastro.scrape (REC-001, REPAIR-001-005)."""

from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel

import rastro
import rastro.application.repair_storage as repair_storage
import rastro.scrape_memory as scrape_memory
from rastro import Outcome, RecipeKey, RecipeState, Source, SQLiteRecipeRepository
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.model_selectors import recipe_from_model
from rastro.scrape import _context

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

URL = "https://acme.example/careers"
V1 = """<main><ul>
<li class="job"><h3 class="title">Engineer</h3><span class="city">Montevideo</span></li>
<li class="job"><h3 class="title">Designer</h3><span class="city">Salto</span></li>
</ul></main>"""
V2 = V1.replace('<h3 class="title">', '<h2 class="heading">').replace("</h3>", "</h2>")
GONE = "<main><p>No openings today.</p></main>"


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3.title::text")]
    city: Annotated[str, rastro.css(".city::text")]


def _scrape(html, memory, repair="propose", schema=Offer):
    return rastro.scrape(html, schema, url=URL, memory=memory, repair=repair)


def _key(schema=Offer):
    seed = recipe_from_model(schema)
    return RecipeKey.for_source(Source(url=URL, locale="en-US"), schema, _context(seed))


def _titles(result):
    return [offer.title for offer in result.items]


def test_success_remembers_the_recipe_and_a_sample(tmp_path):
    memory = tmp_path / "recipes.db"
    first = _scrape(V1, memory)
    assert first.status == Outcome.SUCCESS and not first.recipe_reused
    assert (tmp_path / "recipes.samples.db").exists()
    second = _scrape(V1, memory)
    assert second.recipe_reused and second.repair is None
    assert _titles(second) == ["Engineer", "Designer"]


def test_auto_heals_a_renamed_class_and_serves_the_new_version(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    healed = _scrape(V2, memory, "auto")
    assert healed.status == Outcome.SUCCESS
    assert all(isinstance(item, Offer) for item in healed.items)
    assert _titles(healed) == ["Engineer", "Designer"]
    assert healed.repair is not None and healed.repair.promoted
    assert healed.repair.promoted_version == healed.recipe_version == 2
    assert healed.repair.previous_version == 1
    assert [c.changed_fields for c in healed.repair.candidates] == [("title",)]
    again = _scrape(V2, memory, "auto")
    assert again.repair is None and again.recipe_version == 2 and again.recipe_reused


def test_propose_stores_a_gated_candidate_without_promoting(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    failed = _scrape(V2, memory)
    assert failed.status == Outcome.INVALID_DATA and failed.items == []
    assert failed.repair is not None and not failed.repair.promoted
    (candidate,) = failed.repair.candidates
    assert candidate.decision == "quarantine"
    assert candidate.reasons == ("automatic_promotion_disabled",)
    repository = SQLiteRecipeRepository(memory)
    assert repository.get(_key()).recipe.version == 1
    stored = repository.get(_key(), version=candidate.version)
    assert stored.metadata.state == RecipeState.CANDIDATE


def test_off_returns_the_failure_and_stores_nothing(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    failed = _scrape(V2, memory, "off")
    assert failed.status == Outcome.INVALID_DATA and failed.repair is None
    assert SQLiteRecipeRepository(memory).next_version(_key()) == 2


@pytest.mark.parametrize("decision", ["quarantine", "reject"])
def test_auto_never_promotes_when_a_gate_fails(tmp_path, monkeypatch, decision):
    real = scrape_memory.evaluate_candidate

    def failing_gate(*args, **kwargs):
        evaluation = real(*args, **kwargs)
        assert evaluation.decision == "promote"  # the candidate itself would pass
        return evaluation.model_copy(update={"decision": decision, "reasons": ("gate_failed",)})

    monkeypatch.setattr(scrape_memory, "evaluate_candidate", failing_gate)
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    failed = _scrape(V2, memory, "auto")
    assert failed.status == Outcome.INVALID_DATA and failed.items == []
    assert failed.repair is not None and not failed.repair.promoted
    assert failed.repair.promoted_version is None
    assert [c.decision for c in failed.repair.candidates] == [decision]
    assert SQLiteRecipeRepository(memory).get(_key()).recipe.version == 1


def test_auto_without_a_retained_sample_does_not_promote(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    (tmp_path / "recipes.samples.db").unlink()
    failed = _scrape(V2, memory, "auto")
    assert failed.repair is not None and not failed.repair.promoted
    assert SQLiteRecipeRepository(memory).get(_key()).recipe.version == 1


def test_auto_reports_an_explicit_empty_repair_when_nothing_matches(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    failed = _scrape(GONE, memory, "auto")
    assert failed.status == Outcome.MISSING_DATA and failed.items == []
    assert failed.repair is not None
    assert not failed.repair.promoted and failed.repair.candidates == ()


def test_partial_results_do_not_trigger_repair(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    partial = V1.replace('<h3 class="title">Designer</h3>', "")
    result = _scrape(partial, memory, "auto")
    assert result.status == Outcome.PARTIAL_SUCCESS and result.repair is None


def test_editing_declared_selectors_starts_a_fresh_memory(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)

    class Offer(BaseModel):  # same schema, different selector
        __records__: ClassVar[str] = "li.job"
        title: Annotated[str, rastro.css("h2.heading::text")]
        city: Annotated[str, rastro.css(".city::text")]

    fresh = _scrape(V2, memory, schema=Offer)
    assert fresh.status == Outcome.SUCCESS and not fresh.recipe_reused and fresh.repair is None


def test_a_sample_that_cannot_be_kept_is_reported_not_fatal(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise DatasetStorageError("Dataset operation failed")

    monkeypatch.setattr(repair_storage, "retain_sample", refuse)
    result = _scrape(V1, tmp_path / "recipes.db")
    assert result.status == Outcome.SUCCESS
    assert [issue.code for issue in result.issues] == ["evidence_retention_failed"]


def test_memory_paths_refuse_symlinks(tmp_path):
    target = tmp_path / "real.db"
    link = tmp_path / "recipes.db"
    link.symlink_to(target)
    with pytest.raises(rastro.ConfigurationError, match="symlink"):
        _scrape(V1, link)


def test_discovered_recipes_are_remembered_and_reused(tmp_path):
    class Product(BaseModel):
        name: str
        sku: str

    html = '<script type="application/ld+json">{"name": "Lamp", "sku": "L-1"}</script>'
    memory = tmp_path / "recipes.db"
    first = rastro.scrape(html, Product, memory=memory)
    second = rastro.scrape(html, Product, memory=memory)
    assert first.status == second.status == Outcome.SUCCESS
    assert not first.recipe_reused and second.recipe_reused


def test_auto_refuses_a_relocation_onto_another_fields_values(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory, "auto")
    no_titles = V1.replace('<h3 class="title">Engineer</h3>', "").replace(
        '<h3 class="title">Designer</h3>', ""
    )
    failed = _scrape(no_titles, memory, "auto")
    assert failed.items == [] and failed.repair is not None
    assert not failed.repair.promoted
    (candidate,) = failed.repair.candidates
    assert candidate.decision == "quarantine"
    assert "value_matches_other_field" in candidate.reasons
    assert SQLiteRecipeRepository(memory).get(_key()).recipe.version == 1


def test_propose_reuses_an_identical_stored_candidate(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    first = _scrape(V2, memory)
    second = _scrape(V2, memory)
    assert [c.version for c in first.repair.candidates] == [2]
    assert [c.version for c in second.repair.candidates] == [2]
    assert SQLiteRecipeRepository(memory).next_version(_key()) == 3


def test_auto_promotes_a_previously_proposed_candidate(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    _scrape(V2, memory)
    healed = _scrape(V2, memory, "auto")
    assert healed.status == Outcome.SUCCESS and healed.repair.promoted
    assert healed.repair.promoted_version == healed.recipe_version == 2
    assert SQLiteRecipeRepository(memory).next_version(_key()) == 3


class Item(BaseModel):
    __records__: ClassVar[str] = "div.product"
    name: Annotated[str, rastro.css("h3.name::text")]
    price: Annotated[str, rastro.css("span.price::text")]
    stock: Annotated[str | None, rastro.css("span.stock::text")] = None


def _shop(css_class, missing_stock=None):
    cards = "".join(
        f'<div class="{css_class}"><h3 class="name">Lamp {i}</h3>'
        f'<span class="price">{10 + i}.00</span>'
        + ("" if i == missing_stock else f'<span class="stock">{i} left</span>')
        + "</div>"
        for i in range(3)
    )
    return f"<html><body><main>{cards}</main></body></html>"


@pytest.mark.parametrize("missing", [0, 1])
def test_auto_heals_when_one_record_lacks_an_optional_field(tmp_path, missing):
    """REPAIR-001, SCR-002: an absent optional child does not block relocation."""
    memory = tmp_path / "recipes.db"
    assert _scrape(_shop("product"), memory, "auto", Item).status == Outcome.SUCCESS
    healed = _scrape(_shop("item-card", missing), memory, "auto", Item)
    assert healed.repair is not None and healed.repair.promoted
    assert healed.status == Outcome.SUCCESS
    assert [item.stock for item in healed.items].count(None) == 1
