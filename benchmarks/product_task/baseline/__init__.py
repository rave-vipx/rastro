"""Independent Scrapling + Pydantic agenda loader (DIFF-002)."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel

from benchmarks.baseline import run
from benchmarks.contracts import Mapping
from benchmarks.product_task.baseline.agenda import Performance

ROOT = Path(__file__).resolve().parents[3]


class Source(BaseModel):
    name: str
    path: str
    url: str
    mapping: Mapping


def sources() -> list[Source]:
    return [
        Source.model_validate(value)
        for value in json.loads(
            Path(__file__).with_name("sources.json").read_text(encoding="utf-8")
        )
    ]


def load_source(database: Path, source: Source, *, html: str | None = None) -> str:
    """Validate the whole snapshot before any upsert; retain records on failure."""
    if html is None:
        html = (ROOT / source.path).read_text(encoding="utf-8")
    status, records = run(html, (source.mapping,), Performance)
    if status != "success":
        return status
    keys = [record.performance_id for record in records]
    if len(keys) != len(set(keys)):
        return "ambiguous_identity"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS performances "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL, source TEXT NOT NULL, "
            "source_url TEXT NOT NULL, mapping TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO performances VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "payload=excluded.payload, source=excluded.source, source_url=excluded.source_url, "
            "mapping=excluded.mapping",
            [
                (
                    record.performance_id,
                    record.model_dump_json(),
                    source.name,
                    source.url,
                    source.mapping.model_dump_json(),
                )
                for record in records
            ],
        )
    return status


def read_records(database: Path) -> list[dict[str, str]]:
    with closing(sqlite3.connect(database)) as connection, connection:
        return [
            json.loads(row[0])
            for row in connection.execute("SELECT payload FROM performances ORDER BY key")
        ]
