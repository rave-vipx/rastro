"""Value-consistency gate for relocated fields (REPAIR-002, REPAIR-004)."""

from rastro.domain.value_consistency import (
    OTHER_FIELD,
    SHAPE_CHANGED,
    TYPE_CHANGED,
    value_consistency,
)

HISTORY = [
    {"title": "Platform engineer", "city": "Montevideo", "price": 10, "url": "https://a.example/1"},
    {"title": "Product designer", "city": "Salto", "price": 25, "url": "https://a.example/2"},
]


def test_same_values_and_new_similar_values_pass():
    current = [*HISTORY, {"title": "Data analyst", "city": "Rivera", "price": 7}]
    assert value_consistency(current, HISTORY, ("title", "city", "price", "url")) == ()


def test_values_borrowed_from_another_field_fail():
    current = [{"title": "Salto", "city": "Salto"}]
    assert OTHER_FIELD in value_consistency(current, HISTORY, ("title",))
    same_shape = [{"title": "Montevideo", "city": "Montevideo"}]
    assert value_consistency(same_shape, HISTORY, ("title",)) == (OTHER_FIELD,)


def test_type_changes_fail():
    assert value_consistency([{"price": "ten"}], HISTORY, ("price",)) == (TYPE_CHANGED,)


def test_shape_changes_fail():
    long_text = [{"title": "x" * 200}]
    digits = [{"title": "12345678901234"}]
    no_scheme = [{"url": "/relative/path/to/item"}]
    for rows, field in ((long_text, "title"), (digits, "title"), (no_scheme, "url")):
        assert value_consistency(rows, HISTORY, (field,)) == (SHAPE_CHANGED,)


def test_fields_without_history_or_values_are_not_checked():
    assert value_consistency([{"title": "Anything"}], [{"city": "Salto"}], ("title",)) == ()
    assert value_consistency([{"title": None}], HISTORY, ("title",)) == ()
