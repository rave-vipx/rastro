"""Public agenda page: escaping, link safety, dance badge, grouping, stale marker."""

import runpy
from datetime import UTC, datetime

_module = runpy.run_path("examples/uy-dance-agenda/render_agenda.py")
render = _module["render"]
flatten = _module["flatten"]
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def row(**fields: object) -> dict[str, object]:
    return {"title": "Obra", "venue": "Sala", "starts_on": "2026-11-12", **fields}


def main_of(page: str) -> str:
    return page.split("<main>")[1]


def test_title_is_escaped():
    page = render([row(title="<script>alert(1)</script>")], NOW)
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)" in page


def test_only_http_ticket_links_are_hrefs():
    bad = render([row(ticket_url="javascript:alert(1)")], NOW)
    good = render([row(ticket_url="https://t.example/x?a=1&b=2")], NOW)
    assert "javascript:" not in bad
    assert 'href="https://t.example/x?a=1&amp;b=2"' in good
    assert "Entradas" in good


def test_dance_badge_by_category_not_for_concert():
    assert "Baile" in main_of(render([row(category="Milonga")], NOW))
    assert "Baile" not in main_of(render([row(category="Concierto")], NOW))


def test_days_grouped_in_order_with_spanish_headings():
    rows = [row(title="Bbb", starts_on="2026-11-13"), row(title="Aaa", starts_on="2026-11-12")]
    page = render(rows, NOW)
    assert page.index("jueves 12 de noviembre") < page.index("viernes 13 de noviembre")
    assert page.index("Aaa") < page.index("Bbb")


def test_time_only_when_starts_at_exists():
    page = render([row(title="Con", starts_at="2026-11-12T20:30:00-03:00"), row(title="Sin")], NOW)
    assert page.count("20:30") == 1


def test_stale_marker():
    assert "(sin confirmar hoy)" in render([row(stale=True)], NOW)
    assert "(sin confirmar hoy)" not in render([row()], NOW)


def test_empty_input_message_and_header():
    page = render([], NOW)
    assert "No hay funciones próximas." in page
    assert "Actualizado: miércoles 11 de noviembre, 12:00 (hora de Montevideo)" in page
    assert '<html lang="es">' in page


def test_flatten_marks_unreadable_or_old_observation_stale():
    today = NOW.date()
    fields = {"title": "Obra"}
    assert flatten({"fields": fields, "last_observed_at": "not a date"}, today)["stale"]
    assert flatten({"fields": fields}, today)["stale"]
    assert flatten({"fields": fields, "last_observed_at": "2020-01-01T00:00:00+00:00"}, today)["stale"]
    fresh = flatten({"fields": fields, "last_observed_at": NOW.isoformat()}, today)
    assert "stale" not in fresh
