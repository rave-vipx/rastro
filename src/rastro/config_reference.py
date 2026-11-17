"""Every configurable point of Rastro, generated from code (N18, AGENT-001).

Job keys come from the job models (``config_schema``), keyword arguments from the public
function signatures, flags from the real argparse parser, and grants and environment
variables from ``config_catalog``. ``rastro config``, ``docs/configuration.md``,
``rastro job template`` and the MCP resource ``rastro://configuration`` all render this.
"""

from __future__ import annotations

import argparse
import inspect
import json
from collections.abc import Callable, Iterator
from functools import cache
from typing import Any

from rastro.config_catalog import CLI_DOCS, ENV_VARS, GRANTS, PYTHON_PARAMS
from rastro.config_schema import ConfigEntry, job_entries

__all__ = ["ConfigEntry", "reference"]


@cache
def reference() -> tuple[ConfigEntry, ...]:
    """Job keys, Python keyword arguments, grants, environment variables and CLI flags."""
    from rastro import job_json_schema

    return (
        *job_entries(job_json_schema()),
        *python_entries(),
        *grant_entries(),
        *env_entries(),
        *cli_entries(),
    )


def python_entries() -> Iterator[ConfigEntry]:
    import rastro

    for name, (docs, described) in PYTHON_PARAMS.items():
        function: Callable[..., Any] = getattr(rastro, name)
        for param in inspect.signature(function).parameters.values():
            empty = param.default is inspect.Parameter.empty
            keyword = param.kind is inspect.Parameter.KEYWORD_ONLY
            yield ConfigEntry(
                path=f"rastro.{name}({param.name}=)" if keyword else f"rastro.{name}({param.name})",
                kind="python",
                type=str(param.annotation),
                default=None if empty else _json(param.default),
                required=empty,
                bounds="",
                description=described.get(param.name, ""),
                docs=docs,
            )


def grant_entries() -> Iterator[ConfigEntry]:
    for flag, (env, keyword, text, docs) in GRANTS.items():
        also = ", ".join(item for item in (env and f"env {env}", f"Python {keyword}=") if item)
        yield ConfigEntry(flag, "grant", "operator grant", None, False, also, text, docs)


def env_entries() -> Iterator[ConfigEntry]:
    for name, (text, docs) in ENV_VARS.items():
        yield ConfigEntry(name, "env", "environment variable", None, False, "", text, docs)


def cli_entries() -> Iterator[ConfigEntry]:
    from rastro.cli import _build_parser

    yield from _parser_entries(_build_parser(), "rastro", "")


def _parser_entries(
    parser: argparse.ArgumentParser, command: str, help_text: str
) -> Iterator[ConfigEntry]:
    top = command.split(" ")[1] if " " in command else ""
    docs = CLI_DOCS.get(top, CLI_DOCS[""])
    if command != "rastro":
        yield ConfigEntry(command, "cli", "command", None, False, "", help_text, docs)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: choice.help or "" for choice in action._choices_actions}
            for name, sub in action.choices.items():
                yield from _parser_entries(sub, f"{command} {name}", helps.get(name, ""))
        elif not _skipped(action, command):
            yield _flag_entry(action, command, docs)


def _skipped(action: argparse.Action, command: str) -> bool:
    """``--help``, ``--version`` and the hoisted grant flags repeated on subcommands."""
    if isinstance(action, argparse._HelpAction | argparse._VersionAction):
        return True
    return command != "rastro" and action.default is argparse.SUPPRESS


def _flag_entry(action: argparse.Action, command: str, docs: str) -> ConfigEntry:
    name = action.option_strings[-1] if action.option_strings else action.dest.upper()
    if isinstance(action, argparse._StoreTrueAction):
        kind = "flag"
    elif isinstance(action, argparse._AppendAction):
        kind = f"{action.metavar or 'value'} (repeatable)"
    else:
        kind = str(action.metavar or getattr(action.type, "__name__", "value"))
    default = action.default
    shown = None if default in (None, False, []) or action.required else _json(default)
    choices = "one of " + ", ".join(map(str, action.choices)) if action.choices else ""
    return ConfigEntry(
        path=f"{command} {name}",
        kind="cli",
        type=kind,
        default=shown,
        required=action.required or not action.option_strings,
        bounds=choices,
        description=str(action.help or ""),
        docs=docs,
    )


def _json(value: object) -> str:
    try:
        return json.dumps(value)
    except TypeError:
        return json.dumps(str(value))
