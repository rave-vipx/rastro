"""Objects of a JSON-LD payload: root arrays, @graph containers and ItemList items."""

from collections.abc import Iterator

__all__ = ["jsonld_objects"]


def _list_items(current: dict[str, object], locator: str) -> list[tuple[object, str]]:
    """The ``item`` of each ListItem in an ItemList, with its pointer."""
    elements = current["itemListElement"]
    if not isinstance(elements, list):
        elements = [elements]
    return [
        (element["item"], f"{locator}/itemListElement/{i}/item")
        for i, element in enumerate(elements)
        if isinstance(element, dict) and isinstance(element.get("item"), dict)
    ]


def jsonld_objects(value: object, path: str = "") -> Iterator[tuple[dict[str, object], str]]:
    """Traverse root arrays, @graph containers and ItemList items, never other nested values."""
    pending = [(value, path)]
    while pending:
        current, locator = pending.pop()
        if isinstance(current, list):
            pending.extend(
                (item, f"{locator}/{i}") for i, item in reversed(list(enumerate(current)))
            )
        elif isinstance(current, dict):
            if "@graph" in current:
                pending.append((current["@graph"], f"{locator}/@graph"))
            elif _is_item_list(current):
                pending.extend(reversed(_list_items(current, locator)))
            else:
                yield current, locator


def _is_item_list(current: dict[str, object]) -> bool:
    kind = current.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "ItemList" in kinds and "itemListElement" in current
