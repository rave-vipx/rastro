"""Render a `rastro dataset export --format json` file as a Spanish phone-first agenda page.

Usage: python render_agenda.py export.json > index.html (stdlib only; data is untrusted).
"""

import json
import sys
from datetime import UTC, date, datetime
from html import escape
from itertools import groupby
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Montevideo")
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
DANCE = [
    "tango",
    "folclore",
    "folklore",
    "ballet",
    "danza",
    "dance",
    "milonga",
    "candombe",
    "salsa",
    "flamenco",
    "samba",
    "murga",
    "cumbia",
    "zamba",
    "malambo",
]
CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#66665f;--card:#fff;--line:#e2e0d8;--acc:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#eeeee8;--mut:#a5a59c;
--card:#201f1d;--line:#34332f;--acc:#ff8a80}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
main,header,footer{max-width:40rem;margin:0 auto;padding:.5rem 1rem}
h1{font-size:1.4rem;margin:.8rem 0 .2rem}h2{font-size:1.05rem;margin:1.4rem 0 .4rem;
text-transform:capitalize;border-bottom:1px solid var(--line)}
.upd,.meta,footer{color:var(--mut);font-size:.9rem}a{color:var(--acc)}
.ev{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.6rem .8rem;
margin:.5rem 0;overflow-wrap:anywhere}.t{font-weight:600}.badge{background:var(--acc);
color:var(--bg);border-radius:4px;padding:0 .4rem;font-size:.8rem;margin-left:.4rem}
label{display:block;margin:.6rem 0}.only .ev:not(.dance){display:none}
"""
SCRIPT = (
    "document.getElementById('b').onchange=function(){"
    "document.body.classList.toggle('only',this.checked)}"
)
SOURCES = "SODRE, Sala Zitarrosa, Magnolio Sala, Tickantel"

Row = dict[str, Any]


def day_label(day: date) -> str:
    return f"{DAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]}"


def is_dance(row: Row) -> bool:
    text = f"{row.get('title') or ''} {row.get('category') or ''}".lower()
    return any(word in text for word in DANCE)


def safe_link(url: object) -> str | None:
    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    ok = parts.scheme.lower() in ("http", "https") and parts.netloc and text == text.strip()
    return text if ok else None


def clock(value: object) -> str:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return ""
    return f"{moment.astimezone(ZONE) if moment.tzinfo else moment:%H:%M} "


def has_day(row: Row) -> bool:
    try:
        date.fromisoformat(str(row.get("starts_on")))
    except ValueError:
        return False
    return True


def event_html(row: Row) -> str:
    dance = is_dance(row)
    time = clock(row["starts_at"]) if row.get("starts_at") else ""
    badge = '<span class="badge">Baile</span>' if dance else ""
    meta = [escape(str(row[k])) for k in ("venue", "category") if row.get(k)]
    if row.get("stale"):
        meta.append("(sin confirmar hoy)")
    url = safe_link(row.get("ticket_url"))
    if url:
        meta.append(f'<a href="{escape(url)}" rel="noopener noreferrer">Entradas</a>')
    return (
        f'<div class="ev{" dance" if dance else ""}"><div class="t">{time}'
        f"<span>{escape(str(row.get('title') or ''))}</span>{badge}</div>"
        f'<div class="meta">{" · ".join(meta)}</div></div>'
    )


def body_html(rows: list[Row]) -> str:
    rows = [row for row in rows if has_day(row)]
    if not rows:
        return "<p>No hay funciones próximas.</p>"
    ordered = sorted(rows, key=lambda r: (str(r.get("starts_on")), str(r.get("starts_at") or "")))
    parts = []
    for day, group in groupby(ordered, key=lambda r: str(r.get("starts_on"))):
        parts.append(f"<section><h2>{escape(day_label(date.fromisoformat(day)))}</h2>")
        parts.extend(event_html(row) for row in group)
        parts.append("</section>")
    return "".join(parts)


def render(rows: list[Row], generated_at: datetime) -> str:
    local = generated_at.astimezone(ZONE)
    updated = f"{day_label(local.date())}, {local:%H:%M}"
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Agenda de espectáculos en Montevideo</title><style>{CSS}</style></head><body>"
        "<header><h1>Agenda de espectáculos en Montevideo</h1>"
        f'<div class="upd">Actualizado: {escape(updated)} (hora de Montevideo)</div>'
        '<label><input type="checkbox" id="b"> Solo baile</label></header>'
        f"<main>{body_html(rows)}</main>"
        f"<footer>Fuentes: {SOURCES}. "
        '<a href="https://github.com/rave-vipx/rastro">Hecho con Rastro</a></footer>'
        f"<script>{SCRIPT}</script></body></html>"
    )


def flatten(record: Row, today: date) -> Row:
    """Merge export fields with a stale flag (explicit, else not observed today)."""
    row = dict(record.get("fields") or {})
    seen = record.get("last_observed_at")
    if record.get("stale") or (
        isinstance(seen, str) and datetime.fromisoformat(seen).astimezone(ZONE).date() < today
    ):
        row["stale"] = True
    return row


def main(path: str) -> None:
    now = datetime.now(UTC)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    today = now.astimezone(ZONE).date()
    rows = [flatten(r, today) for r in data.get("records", [])]
    sys.stdout.write(render(rows, now))


if __name__ == "__main__":
    main(sys.argv[1])
