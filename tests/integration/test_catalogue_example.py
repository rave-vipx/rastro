"""The catalogue example extracts synthetic HTML and reuses its recipe offline."""

import pytest
from pydantic import ValidationError

from examples.catalogue import CONTEXT, RECIPE, SOURCE, Book
from rastro import Outcome, Rastro, SQLiteRecipeRepository


def test_catalogue_contract_and_recipe_survive_restart(tmp_path) -> None:
    html = (
        '<div class="product_main"><h1>Synthetic Book</h1><p class="price_color">£12.50</p></div>'
    )
    database = tmp_path / "recipes.db"
    for reused in (False, True):
        engine = Rastro(repository=SQLiteRecipeRepository(database))
        result = engine.extract_html(
            html, source=SOURCE, schema=Book, context=CONTEXT, recipe=None if reused else RECIPE
        )
        assert result.status == Outcome.SUCCESS
        assert result.recipe_reused is reused
        assert result.records[0].data.name == "Synthetic Book"
        assert str(result.records[0].data.price_gbp) == "12.50"
        assert result.records[0].evidence.keys() == {"name", "price_gbp"}


@pytest.mark.parametrize("value", ["12.50", "$12.50", "£-12.50", "£1,250.00", "£NaN", "£12.5"])
def test_example_rejects_ambiguous_or_invalid_currency(value) -> None:
    with pytest.raises(ValidationError):
        Book.model_validate({"name": "Synthetic Book", "price_gbp": value})


def test_catalogue_command_reuses_a_recipe_without_network(tmp_path, monkeypatch, capsys) -> None:
    import json

    from examples.catalogue import main

    monkeypatch.setattr("sys.argv", ["catalogue.py", "--database", str(tmp_path / "recipes.db")])
    for reused in (False, True):
        assert main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["recipe_reused"] is reused
        assert result["records"][0]["data"]["name"] == "Synthetic Book"
