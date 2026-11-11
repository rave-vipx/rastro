"""Studio lists only single-selector fields as editable (P-008, SCR-*)."""

from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from rastro.studio.editing import list_selectors


def test_pointer_list_fields_are_not_offered_for_editing():
    recipe = SimpleNamespace(fields={"name": "/name", "date": ["/startDate", "/date"]})
    job = SimpleNamespace(recipe=recipe, sources=())
    assert list_selectors(job) == [{"source": None, "field": "name", "selector": "/name"}]
