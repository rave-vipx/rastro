"""Walk the job JSON Schema into one ``ConfigEntry`` per key (AGENT-001, N18).

Names, types, defaults and bounds come from the Pydantic models through
``job_json_schema``; descriptions come from the field or ``config_descriptions``. Each
nested model is expanded once, at its shallowest path; later uses point back to it, which
also ends the ``fallbacks`` recursion.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any

from rastro.config_catalog import JOB_DOCS
from rastro.config_descriptions import DESCRIPTIONS, SHARED

type Schema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    """One configurable point: a job key, a keyword argument, a CLI flag or a variable."""

    path: str
    kind: str  # job | python | cli | grant | env
    type: str
    default: str | None  # JSON text; None when there is no default
    required: bool
    bounds: str
    description: str
    docs: str
    variant: str = ""  # 'strategy = "css"' when the key exists only in that variant
    same_as: str = ""  # path whose keys this table repeats


@dataclass(frozen=True, slots=True)
class _Child:
    suffix: str  # "" (table), "[]" (array of tables) or ".<name>" (map of tables)
    members: tuple[tuple[str, str], ...]  # (definition name, variant label)
    scalars: tuple[str, ...]  # non-table alternatives, such as "string"


def job_entries(schema: Schema) -> tuple[ConfigEntry, ...]:
    """Every key of the job file, in file order, with nested tables after their parent."""
    defs: dict[str, Schema] = schema["$defs"]
    owners = _owners(schema, defs)
    entries: list[ConfigEntry] = []
    _walk(schema, "Job", "", "", _Context(defs, owners, set(), entries))
    return tuple(entries)


def _owners(schema: Schema, defs: dict[str, Schema]) -> dict[str, str]:
    """Definition name -> the shallowest path that expands it (breadth-first)."""
    owners: dict[str, str] = {}
    queue: deque[tuple[Schema, str]] = deque([(schema, "")])
    while queue:
        model, prefix = queue.popleft()
        for name, spec in model.get("properties", {}).items():
            child = _child(spec, defs)
            for member, _ in child.members:
                if member not in owners:
                    owners[member] = prefix + name + child.suffix
                    queue.append((defs[member], prefix + name + child.suffix + "."))
    return owners


@dataclass(frozen=True, slots=True)
class _Context:
    defs: dict[str, Schema]
    owners: dict[str, str]
    expanded: set[str]
    out: list[ConfigEntry]

    def same_as(self, child: _Child, path: str) -> str:
        """The path that already lists these keys, or "" when this path expands them."""
        for member, _ in child.members:
            owner = self.owners[member]
            if owner != path + child.suffix or member in self.expanded:
                return owner
        return ""


def _walk(model: Schema, model_name: str, prefix: str, variant: str, ctx: _Context) -> None:
    required = set(model.get("required", ()))
    for name, spec in model.get("properties", {}).items():
        path = prefix + name
        child = _child(spec, ctx.defs)
        core = _resolve(_strip_null(spec)[0], ctx.defs)
        same = ctx.same_as(child, path)
        ctx.out.append(
            ConfigEntry(
                path=path,
                kind="job",
                type=_type_text(core, child),
                default=None if "default" not in spec else json.dumps(spec["default"]),
                required=name in required,
                bounds=_bounds(core),
                description=_describe(model_name, name, spec),
                docs=_docs(path),
                variant=variant,
                same_as=same,
            )
        )
        if same:
            continue
        ctx.expanded.update(member for member, _ in child.members)
        for member, label in child.members:
            sub_variant = label if len(child.members) > 1 else variant
            _walk(ctx.defs[member], member, path + child.suffix + ".", sub_variant, ctx)


def _strip_null(spec: Schema) -> tuple[Schema, bool]:
    options = spec.get("anyOf")
    if not options or not any(item.get("type") == "null" for item in options):
        return spec, False
    rest = [item for item in options if item.get("type") != "null"]
    return (rest[0] if len(rest) == 1 else {"anyOf": rest}), True


def _resolve(spec: Schema, defs: dict[str, Schema]) -> Schema:
    """Inline a reference to a non-table definition, such as an enum."""
    if "$ref" in spec:
        target = defs[spec["$ref"].rsplit("/", 1)[-1]]
        if "properties" not in target:
            return {**target, **{k: v for k, v in spec.items() if k != "$ref"}}
    return spec


def _child(spec: Schema, defs: dict[str, Schema]) -> _Child:
    core, _ = _strip_null(spec)
    suffix, inner = "", core
    if isinstance(core.get("items"), dict):
        suffix, inner = "[]", core["items"]
    elif isinstance(core.get("additionalProperties"), dict) and "$ref" in json.dumps(
        core["additionalProperties"]
    ):
        suffix, inner = ".<name>", core["additionalProperties"]
    members: list[tuple[str, str]] = []
    scalars: list[str] = []
    _collect(inner, defs, members, scalars, None)
    if not members:
        return _Child("", (), ())
    return _Child(suffix, tuple(members), tuple(scalars))


def _collect(
    spec: Schema,
    defs: dict[str, Schema],
    members: list[tuple[str, str]],
    scalars: list[str],
    discriminator: str | None,
) -> None:
    if "$ref" in spec:
        name = spec["$ref"].rsplit("/", 1)[-1]
        target = defs[name]
        if "properties" in target:
            if name not in {member for member, _ in members}:
                members.append((name, _label(name, target, discriminator)))
            return
        spec = target
    options = spec.get("oneOf") or spec.get("anyOf")
    if options:
        key = spec.get("discriminator", {}).get("propertyName", discriminator)
        for option in options:
            _collect(option, defs, members, scalars, key)
    elif "type" in spec:
        scalars.append(str(spec["type"]))


def _label(name: str, model: Schema, discriminator: str | None) -> str:
    props: dict[str, Schema] = model["properties"]
    keys = [discriminator] if discriminator in props else list(props)
    for key in keys:
        spec = props[key]
        if "const" in spec:
            return f'{key} = "{spec["const"]}"'
        if key == discriminator and "enum" in spec:
            return f"{key} = " + " or ".join(f'"{value}"' for value in spec["enum"])
    return name


def _type_text(core: Schema, child: _Child) -> str:
    if child.members:
        table = {"": "table", "[]": "array of tables", ".<name>": "map of tables"}[child.suffix]
        return " or ".join([table, *child.scalars])
    return _scalar_type(core)


def _scalar_type(spec: Schema) -> str:
    if "const" in spec:
        return "integer" if isinstance(spec["const"], int) else "string"
    if "anyOf" in spec:
        return " or ".join(dict.fromkeys(_scalar_type(item) for item in spec["anyOf"]))
    kind = spec.get("type", "value")
    if kind == "array":
        if "prefixItems" in spec:
            return f"array of {len(spec['prefixItems'])} strings"
        return f"array of {_scalar_type(spec.get('items', {}))}"
    if kind == "object":
        return f"table of {_scalar_type(spec.get('additionalProperties', {}))}"
    return str(kind)


_BOUND_KEYS = (
    ("minimum", ">="),
    ("ge", ">="),
    ("exclusiveMinimum", ">"),
    ("gt", ">"),
    ("maximum", "<="),
    ("le", "<="),
    ("minLength", "length >="),
    ("maxLength", "length <="),
    ("minItems", "items >="),
    ("maxItems", "items <="),
    ("minProperties", "keys >="),
    ("maxProperties", "keys <="),
)


def _bounds(spec: Schema) -> str:
    own = [f"{label} {spec[key]}" for key, label in _BOUND_KEYS if key in spec]
    if "enum" in spec:
        own.insert(0, "one of " + ", ".join(str(value) for value in spec["enum"]))
    if "const" in spec:
        own.insert(0, f"always {json.dumps(spec['const'])}")
    if "pattern" in spec:
        own.append(f"pattern {spec['pattern']}")
    nested = [_bounds(item) for item in spec.get("anyOf", ())]
    return "; ".join(text for text in (", ".join(own), *nested) if text)


def _describe(model_name: str, name: str, spec: Schema) -> str:
    text = spec.get("description") or DESCRIPTIONS.get(f"{model_name}.{name}")
    return str(text or SHARED.get(name, ""))


def _docs(path: str) -> str:
    plain = path.replace("[]", "").replace(".<name>", "")
    keys = [key for key in JOB_DOCS if plain == key or plain.startswith(key + ".")]
    return JOB_DOCS[max(keys, key=len, default="")]
