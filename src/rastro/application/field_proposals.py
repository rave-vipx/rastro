"""Propose extraction fields and selectors from observed repeated structures (DISC-003, P-002)."""

from __future__ import annotations

import keyword
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel

import rastro.domain.job_fields as jf
from rastro.application.engine import Rastro
from rastro.application.model_choice import resolve_model
from rastro.application.suggest import LOCAL_URL, suggest
from rastro.domain.models import ConfigurationError, Contract, CssRecipe, Limits, Outcome, Source
from rastro.domain.page_outline import clip_untrusted
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.domain.suggest import SuggestionError, infer_field_type, normalize_text

MAX_PROPOSED_FIELDS = 12
_TYPED: dict[str, type[jf.JobField]] = {
    "integer": jf.IntegerField,
    "decimal": jf.DecimalField,
    "date": jf.DateField,
    "url": jf.UrlField,
}
_RESERVED = frozenset(dir(BaseModel)) | {"model_config", "model_fields"}
_ALIASES = {
    "title": ("title", "name", "heading", "h1", "h2", "h3"),
    "name": ("name", "title", "heading", "h1", "h2", "h3"),
    "url": ("url", "link", "ticket", "href"),
    "link": ("link", "url", "ticket", "href"),
    "date": ("date", "starts_on", "posted", "time"),
}


class FieldProposal(Contract):
    """One observed field candidate with its selector, inferred type and example value."""

    name: str
    type: jf.JobFieldType = "string"
    selector: str
    example: str
    record_selector: str = ""


class ProposedFields(tuple[FieldProposal, ...]):
    """Tuple of ``FieldProposal`` items carrying the verified ``CssRecipe``."""

    recipe: CssRecipe
    record_selector: str

    def __new__(cls, items: Sequence[FieldProposal], recipe: CssRecipe) -> ProposedFields:
        inst = super().__new__(cls, items)
        inst.recipe, inst.record_selector = recipe, recipe.record_selector
        return inst


def _clean_ident(raw: str) -> str | None:
    ident = re.sub(r"[^a-zA-Z0-9_]", "_", raw.strip()).strip("_").lower()[:48]
    if not ident or not ident[0].isalpha():
        return None
    return f"{ident}_field" if keyword.iskeyword(ident) or ident in _RESERVED else ident


def _field_name(classes: Sequence[str], tag: str, attr: str | None) -> str:
    if classes and (cls_name := _clean_ident(classes[0])):
        return cls_name
    if attr in {"href", "datetime", "src"}:
        return {"href": "url", "datetime": "date", "src": "image_url"}[attr]
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "title"
    return {"time": "date", "a": "link"}.get(tag, _clean_ident(tag) or "field")


def _discover_from_dom(
    html: str, records_min: int, limits: Limits
) -> list[tuple[str, list[tuple[str, str, list[str], tuple[str, ...]]]]]:
    from rastro.adapters.page_outline import LxmlPageOutliner
    from rastro.adapters.scrapling import _parse_html
    from rastro.adapters.suggest_dom import elements, stable_classes, tag_of
    from rastro.adapters.suggest_fields import MAX_FIELD_SELECTORS, relative_selectors

    outline = LxmlPageOutliner().outline(html, limits)
    if not html.strip():
        return []
    page = _parse_html(html)
    selectors = [c.selector for c in outline.containers]
    for parent in elements(page._root):
        if ptag := tag_of(parent):
            psig = ptag + "".join(f".{c}" for c in stable_classes(parent))
            counts: dict[str, int] = {}
            for ch in parent:
                if isinstance(ch.tag, str) and (ctag := tag_of(ch)):
                    csig = ctag + "".join(f".{c}" for c in stable_classes(ch))
                    counts[csig] = counts.get(csig, 0) + 1
            for cs, n in counts.items():
                if n >= records_min and f"{psig} > {cs}" not in selectors:
                    selectors.append(f"{psig} > {cs}")
    results = []
    for rec_sel in selectors:
        try:
            records = tuple(page.css(rec_sel))
        except Exception:
            continue
        if not records_min <= len(records) <= limits.max_records:
            continue
        first_root, found, used = records[0]._root, [], set[str]()
        for node in elements(first_root)[1:]:
            tag, classes = tag_of(node) or "field", tuple(stable_classes(node))
            attrs = [a for a in ("datetime", "href", "src", "content") if node.get(a)]
            specs: list[tuple[str, str | None]] = [(f"::attr({a})", a) for a in attrs]
            if (node.text or "").strip() and "datetime" not in attrs and "href" not in attrs:
                specs.append(("::text", None))
            for suffix, attr in specs:
                for rel in relative_selectors(first_root, node)[:MAX_FIELD_SELECTORS]:
                    sel = rel + suffix
                    if not rel or sel in used:
                        continue
                    vals = [
                        [
                            normalize_text(str(v))
                            for v in r.css(sel).getall()
                            if normalize_text(str(v))
                        ]
                        for r in records
                        if len(r.css(rel)) <= 1
                    ]
                    if len(vals) == len(records) and all(len(v) == 1 for v in vals):
                        used.add(sel)
                        found.append(
                            (_field_name(classes, tag, attr), sel, [v[0] for v in vals], classes)
                        )
                        break
        if found:
            results.append((rec_sel, found[:MAX_PROPOSED_FIELDS]))
    return results


def _verify_and_type(
    html: str,
    rec_sel: str,
    chosen: Sequence[tuple[str, str, list[str]]],
    url: str | None,
    limits: Limits,
) -> ProposedFields | None:
    recipe = check_untrusted_recipe(
        CssRecipe(record_selector=rec_sel, fields={n: s for n, s, _ in chosen})
    )
    src, client = Source(url=url or LOCAL_URL), Rastro(limits=limits)
    typed: list[jf.JobField] = [jf.StringField(name=n) for n, _, _ in chosen]
    base = client.extract_html(html, source=src, schema=jf.build_job_schema(typed), recipe=recipe)
    if base.status is not Outcome.SUCCESS or not base.records:
        return None
    for idx, (name, sel, values) in enumerate(chosen):
        is_url_attr = bool(url and sel.endswith(("::attr(href)", "::attr(src)")))
        samples = [urljoin(url or "", v) for v in values] if is_url_attr else values
        if (kind := infer_field_type(samples)) != "string":
            trial = [*typed[:idx], _TYPED[kind](name=name), *typed[idx + 1 :]]
            res = client.extract_html(
                html, source=src, schema=jf.build_job_schema(trial), recipe=recipe
            )
            if (res.status, len(res.records)) == (base.status, len(base.records)):
                typed = trial
    items = [
        FieldProposal(
            name=f.name,
            type=f.type,
            selector=s,
            example=clip_untrusted(v[0], 120),
            record_selector=rec_sel,
        )
        for f, (_, s, v) in zip(typed, chosen, strict=True)
    ]
    return ProposedFields(items, recipe)


def propose_fields(html: str, **opts: Any) -> ProposedFields:
    url: str | None = opts.get("url")
    records_min = int(opts.get("records_min", 2))
    limits: Limits = opts.get("limits") or Limits()
    req = tuple(dict.fromkeys(f.strip() for f in opts.get("fields", ()) if f.strip()))
    try:
        for name in req:
            jf.StringField(name=name)
    except Exception:
        raise ConfigurationError("Field names must be valid identifiers") from None
    for rec_sel, discovered in _discover_from_dom(html, records_min, limits):
        if req:
            rem, chosen = list(discovered), list[tuple[str, str, list[str]]]()
            for r in req:
                targets = (r.lower(), *_ALIASES.get(r.lower(), ()))
                hit = next(
                    (i for i in rem if i[0] in targets or any(t in i[3] for t in targets)), None
                )
                if hit is not None:
                    rem.remove(hit)
                    chosen.append((r, hit[1], hit[2]))
            if len(chosen) != len(req):
                continue
        else:
            taken, chosen = set[str](), []
            for base, sel, vals, _ in discovered:
                name, idx = base, 2
                while name in taken:
                    name, idx = f"{base}_{idx}", idx + 1
                taken.add(name)
                chosen.append((name, sel, vals))
        if (built := _verify_and_type(html, rec_sel, chosen, url, limits)) is not None:
            return built
    port = resolve_model(opts.get("model"), allow_remote=bool(opts.get("allow_remote", False)))
    if port is not None and req:
        found = suggest(
            html,
            [jf.StringField(name=n) for n in req],
            url=url,
            records_min=records_min,
            model=port,
        )
        if found.preview.records:
            first = found.preview.records[0].data.model_dump(mode="json")
            rec = found.recipe
            items = [
                FieldProposal(
                    name=n,
                    selector=rec.fields[n],
                    example=clip_untrusted(str(first.get(n, "")), 120),
                    record_selector=rec.record_selector,
                )
                for n in req
                if n in rec.fields
            ]
            return ProposedFields(items, rec)
    raise SuggestionError(
        f"No repeated structure with at least {records_min} observable records was found "
        f"(outcome: {Outcome.INSUFFICIENT_EVIDENCE})"
    )
