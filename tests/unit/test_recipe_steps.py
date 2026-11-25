"""Recipes carry page steps as data: no script, cheap selectors, a capability for others."""

from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from rastro.application.capability_gate import CapabilityRefused, require_capabilities
from rastro.application.operator_trust import input_origin
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError
from rastro.domain.recipes import CssRecipe, Recipe
from rastro.domain.selector_policy import check_untrusted_recipe

BASE = {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}}


def job_text(step: str) -> str:
    return (
        'format_version = 1\n[acquisition]\nallowed_hosts = ["shop.example"]\n'
        'render = "browser"\n[[sources]]\nurl = "https://shop.example/list"\n'
        '[recipe]\nstrategy = "css"\nrecord_selector = "article"\n'
        f"[[recipe.steps]]\n{step}\n"
        '[recipe.fields]\nname = "h2::text"\n'
        '[[fields]]\nname = "name"\ntype = "string"\n'
    )


def test_steps_parse_from_a_job_file():
    job = parse_job(job_text('action = "click"\nselector = "button.more"'), format="toml")
    assert isinstance(job.recipe, CssRecipe)
    assert [step.action for step in job.recipe.steps] == ["click"]


@pytest.mark.parametrize(
    "step",
    [
        'action = "click"\nselector = "button"\njs = "document.body.remove()"',
        'action = "script"\nselector = "button"',
        'action = "evaluate"\nexpression = "1"',
        'action = "type"\nselector = "input"\ntext = "x"\ncallable = "os.system"',
    ],
)
def test_no_script_or_callable_in_steps(step):
    with pytest.raises((ValidationError, ConfigurationError, ValueError)):
        parse_job(job_text(step), format="toml")


def test_a_recipe_without_steps_serializes_as_before():
    recipe = Recipe.model_validate(BASE)
    assert "steps" not in recipe.model_dump()
    with_steps = Recipe.model_validate(
        {**BASE, "steps": [{"action": "click", "selector": "button.more"}]}
    )
    assert with_steps.model_dump()["steps"][0]["action"] == "click"


@pytest.mark.parametrize("selector", ["xpath=//button", "text=More", "a >> b", "css=a"])
def test_step_selectors_stay_css_for_every_origin(selector):
    with pytest.raises(ValidationError):
        Recipe.model_validate({**BASE, "steps": [{"action": "click", "selector": selector}]})


@pytest.mark.parametrize(
    "step",
    [
        {"action": "click", "selector": "div:has(> a)"},
        {"action": "click", "selector": "button", "wait_for": "p ~ p ~ p"},
        {"action": "wait", "selector": "li + li"},
        {"action": "follow", "selector": "a:has(b)::attr(href)"},
    ],
)
def test_untrusted_step_selectors_follow_the_cost_policy(step):
    recipe = Recipe.model_validate({**BASE, "steps": [step]})
    with pytest.raises(ConfigurationError):
        check_untrusted_recipe(recipe)


def test_fallback_recipes_cannot_carry_steps():
    with pytest.raises(ValidationError):
        Recipe.model_validate({**BASE, "fallbacks": [{**BASE, "steps": [{"action": "scroll"}]}]})


def test_steps_from_bundle_need_page_actions_capability(monkeypatch):
    for name in ("RASTRO_ALLOW_CAPABILITIES", "RASTRO_ALLOW_UNTRUSTED"):
        monkeypatch.delenv(name, raising=False)
    job = parse_job(job_text('action = "click"\nselector = "button.more"'), format="toml")
    assert "page_actions" in require_capabilities(job).granted  # the operator's own job
    with input_origin("bundle"), pytest.raises(CapabilityRefused) as caught:
        require_capabilities(job)
    assert "page_actions" in caught.value.decision.refused


def test_steps_keys_are_not_reread_as_toml_strings():
    # A step's text is literal: TOML values are data, never expanded or interpolated.
    parsed = tomllib.loads(job_text('action = "type"\nselector = "input"\ntext = "$(id)"'))
    job = parse_job(job_text('action = "type"\nselector = "input"\ntext = "$(id)"'), format="toml")
    assert parsed["recipe"]["steps"][0]["text"] == job.recipe.steps[0].text == "$(id)"
