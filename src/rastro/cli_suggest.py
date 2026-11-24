"""`rastro suggest` and `rastro new`: recipes and jobs from examples, sites or a goal (JOB-001)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from rastro.application.job_loader import _read_local_source
from rastro.application.job_new import new_job
from rastro.application.job_new_sites import new_job_from_goal, new_job_from_sites
from rastro.application.model_choice import resolve_model
from rastro.application.source_proposals import clean_proposal, default_acquirer
from rastro.application.suggest import suggest
from rastro.cli_explore import add_explore_options, explore_command
from rastro.cli_model import add_model_options
from rastro.cli_suggest_output import _print_goal_result, _table, suggestion_payload
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.job_fields import StringField
from rastro.domain.models import ConfigurationError, Limits
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.model import ModelPort
from rastro.ports.search import SearchProvider


def add_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    sp = subcommands.add_parser(
        "suggest", help="Suggest a CSS recipe from a saved page and example values"
    )
    sp.add_argument("page", type=Path, help="Saved HTML page")
    _add_example_options(sp)
    sp.add_argument(
        "--output", choices=("json", "table"), default="json", help="JSON or a human table"
    )
    new = subcommands.add_parser(
        "new", help="Create a job file from a saved page, known sites or a goal"
    )
    new.add_argument("file", type=Path, nargs="?", help="New .toml job file; never overwritten")
    new.add_argument(
        "-o", "--output-file", dest="output_file", type=Path, help="New .toml job file"
    )
    new.add_argument("--from", dest="page", type=Path, help="Saved HTML page")
    new.add_argument("--goal", help="Research goal text for source discovery")
    add_explore_options(new)
    new.add_argument(
        "--site", nargs="+", action="extend", default=[], metavar="URL", help="Page URLs to scrape"
    )
    new.add_argument(
        "--fields", action="append", default=[], metavar="A,B", help="Field names to propose"
    )
    new.add_argument(
        "--search", choices=("searxng", "model"), help="How --goal finds candidate sources"
    )
    new.add_argument(
        "--approve",
        nargs="+",
        action="extend",
        default=[],
        metavar="HOST",
        help="Hosts you approve for fetching",
    )
    _add_example_options(new)
    new.add_argument("--dataset", help="Dataset path for approved records, relative to the job")
    new.add_argument("--identity", action="append", default=[], help="Record key field")
    new.add_argument(
        "--recipe-memory",
        metavar="FILE",
        help="Recipe store relative to the job, enabling repair (folder must exist)",
    )
    new.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write the job even if the recipe preview is only a partial success",
    )


def _add_example_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="Address the page was saved from")
    parser.add_argument(
        "--example",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Example value; repeating a field starts the next example record",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME",
        help="Field to extract without an example (requires --model)",
    )
    add_model_options(parser, "propose the recipe; examples become optional")
    parser.add_argument("--records-min", type=int, default=2, help="Minimum records (default 2)")


def parse_examples(items: list[str]) -> list[dict[str, str]]:
    """``FIELD=VALUE`` items as example records; a repeated field starts a new record."""
    if not items:
        return []
    records: list[dict[str, str]] = [{}]
    for item in items:
        name, sep, value = item.partition("=")
        name = name.strip()
        if not sep or not name:
            raise ConfigurationError("--example must look like FIELD=VALUE")
        if name in records[-1]:
            records.append({})
        records[-1][name] = value
    return records


def _model(args: argparse.Namespace) -> ModelPort | None:
    if not args.example and args.model is None:
        raise ConfigurationError("Give --example values, or --model with --field names")
    if args.field and args.model is None:
        raise ConfigurationError("--field without an example requires --model")
    return resolve_model(args.model, allow_remote=args.allow_remote)


def _split_csv(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p.strip() for it in items for p in it.split(",") if p.strip()))


def _default_search_provider(_args: argparse.Namespace) -> SearchProvider | None:
    return None


def _default_acquirer(policy: AcquisitionPolicy) -> AcquisitionPort:
    return default_acquirer(policy)


def _acquirer_for_hosts(hosts: tuple[str, ...], *, allow_http: bool = False) -> AcquisitionPort:
    try:
        policy = AcquisitionPolicy(allowed_hosts=hosts, allow_http=allow_http)
    except ValidationError:
        raise ConfigurationError("Approved hosts must be exact hostnames or IP addresses") from None
    return _default_acquirer(policy)


def suggest_command(args: argparse.Namespace) -> int:
    model, examples = _model(args), parse_examples(args.example)
    html = _read_local_source(args.page, Limits().max_html_bytes, "html")
    names = dict.fromkeys([*(n for ex in examples for n in ex), *args.field])
    try:
        fields = [StringField(name=name) for name in names]
    except ValueError:
        raise ConfigurationError(
            "Example field names must start with a letter and use letters, digits or _"
        ) from None
    found = suggest(
        html, fields, examples=examples, url=args.url, records_min=args.records_min, model=model
    )
    payload = suggestion_payload(found)
    if args.output == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload["recipe"], indent=2))
        print(_table(payload, list(names)))
    return 0


def new_command(args: argparse.Namespace) -> int:
    if args.file is not None and args.output_file is not None:
        raise ConfigurationError("Give the job file once, either positionally or with -o")
    target = args.output_file or args.file
    chosen = (args.page is not None, bool(args.site), args.goal is not None, args.explore)
    if sum(item is not None and item is not False for item in chosen) != 1:
        raise ConfigurationError(
            "Specify exactly one of --from PAGE, --site URL, --goal TEXT or --explore JOB"
        )
    if args.explore is not None:
        return explore_command(args, target)
    common = {
        "fields": _split_csv([*args.field, *args.fields]),
        "records_min": args.records_min,
        "dataset": args.dataset,
        "identity": tuple(args.identity),
        "recipe_memory": args.recipe_memory,
        "allow_partial": args.allow_partial,
    }
    if args.page is not None:
        if target is None:
            raise ConfigurationError("Give the new .toml job file path")
        ex = parse_examples(args.example)
        path = new_job(
            target, page=args.page, examples=ex, url=args.url, model=_model(args), **common
        )
        print(json.dumps({"schema_version": 1, "created": str(path)}))
        return 0
    if args.goal is not None:
        approved = _split_csv(args.approve)
        acq = _acquirer_for_hosts(approved) if approved else None
        res = new_job_from_goal(
            target,
            goal=args.goal,
            approve=approved,
            search=args.search,
            provider=_default_search_provider(args),
            model=args.model,
            acquisition=acq,
            allow_remote=args.allow_remote,
            **common,
        )
        return _print_goal_result(res)
    if target is None:
        raise ConfigurationError("Pass -o FILE.toml to write the job")
    site_urls = _split_csv(args.site)
    cleaned = [clean_proposal(u, "", "search") for u in site_urls]
    if not cleaned or any(p is None for p in cleaned):
        raise ConfigurationError("Every --site must be a valid public HTTP(S) URL")
    hosts = tuple(dict.fromkeys(p.host for p in cleaned if p is not None))
    allow_http = any(p.url.startswith("http://") for p in cleaned if p is not None)
    acq = _acquirer_for_hosts(hosts, allow_http=allow_http)
    res = new_job_from_sites(
        target,
        sites=site_urls,
        model=args.model,
        acquisition=acq,
        allow_remote=args.allow_remote,
        **common,
    )
    return _print_goal_result(res)
