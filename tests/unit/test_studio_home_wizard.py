"""Studio Home (projects, empty states) and the four-step wizard (N1, N2, N5)."""

from __future__ import annotations

from lxml import html

from tests.unit.studio_js import STATIC, run_node

PROJECTS = {
    "projects": [
        {"file": "agenda.toml", "name": "agenda", "health": "warnings",
         "last_run": "2026-11-21T22:00:00-03:00", "records": 74, "sources_total": 5,
         "sources_with_problems": 1},
        {"file": "news/local.toml", "name": "local", "health": "failing", "last_run": None,
         "records": 0, "sources_total": 1, "sources_with_problems": 1},
    ],
    "other_databases": [],
    "truncated": False,
}  # fmt: skip

SETUP = """
const home = await import("STATIC/home.js");
const opened = [];
home.initHome({ openJob: async (f) => opened.push(f), switchView: (v) => opened.push(v) });
const list = document.getElementById("home-list");
"""


def test_home_shows_one_card_per_project_with_health_in_words() -> None:
    """N1/N5: name, health word + shape, last update, records, problems, View results."""
    run_node(
        SETUP
        + """
reply({"/api/projects": [200, PROJECTS]});
await home.loadHome();
const cards = walk(list).filter((n) => n.className === "project-card");
assert.equal(cards.length, 2);
const text = list.textContent;
assert.ok(text.includes("With warnings") && text.includes("Failing"));
assert.ok(text.includes("74") && text.includes("1 of 5") && text.includes("never"));
assert.match(classes(list), /ico-triangle/);
assert.match(classes(list), /ico-octagon/);
const view = buttons(cards[0]).find((b) => b.textContent === "View results");
await view.click();
assert.deepEqual(opened, ["agenda.toml", "results"]);
""",
        PROJECTS=PROJECTS,
    )


def test_empty_folder_and_unknown_databases_are_explained() -> None:
    """N1: an empty folder offers both starts; unknown SQLite files are named, not opened."""
    run_node(
        SETUP
        + """
reply({"/api/projects": [200, {projects: [], other_databases: [], truncated: false}]});
await home.loadHome();
assert.ok(list.textContent.includes("No projects in this folder yet"));
const labels = buttons(list).map((b) => b.textContent);
assert.deepEqual(labels, ["Start from a URL", "Connect an export"]);
reply({"/api/projects": [200, {projects: [], other_databases: ["old/events.sqlite"]}]});
await home.loadHome();
assert.ok(list.textContent.includes("doesn't recognize"));
assert.ok(list.textContent.includes("old/events.sqlite"));
assert.ok(!list.textContent.includes("No projects in this folder yet"));
"""
    )


def test_missing_projects_route_falls_back_without_inventing_health() -> None:
    """N5: an older Studio lists job files with "Status unknown"; a 500 is an error state."""
    run_node(
        SETUP
        + """
reply({"/api/jobs": [200, {root: "r", files: ["agenda.toml"], truncated: false}]});
await home.loadHome();
const text = list.textContent;
assert.ok(text.includes("lists projects only") && text.includes("Status unknown"));
assert.ok(text.includes("Not reported") && !text.includes("Up to date"));
reply({"/api/projects": [500, {ok: false, error: "internal"}]});
await home.loadHome();
assert.ok(list.textContent.includes("Projects are not available"));
assert.ok(!list.textContent.includes("No projects in this folder yet"));
"""
    )


def test_wizard_shows_one_step_at_a_time_and_names_files_from_the_project() -> None:
    """N1: four steps, one decision each; file names derive from the project name."""
    run_node("""
const wizard = await import("STATIC/wizard.js");
wizard.goStep(3);
const shown = [1, 2, 3, 4].filter((n) => !document.getElementById(`wiz-step-${n}`).hidden);
assert.deepEqual(shown, [3]);
assert.equal(document.focused, document.getElementById("wiz-next-3"));
assert.equal(wizard.slug("Agenda de fiestas · Valle Azul"), "agenda-de-fiestas-valle-azul");
assert.equal(wizard.slug("../../etc"), "etc");
""")
    page = html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))
    steps = page.xpath("//div[starts-with(@id, 'wiz-step-')]")
    assert [s.get("id") for s in steps] == [f"wiz-step-{n}" for n in range(1, 5)]
    assert [s.get("hidden") is not None for s in steps] == [False, True, True, True]
    assert page.xpath("//ol[@class='stepper']/li[@aria-current='step']/@data-step") == ["1"]
    files = page.get_element_by_id("new-file")
    assert files.xpath("ancestor::details"), "file names are an advanced detail"
