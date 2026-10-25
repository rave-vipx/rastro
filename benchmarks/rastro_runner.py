"""Rastro integration for the same preconfigured extraction workload as the baseline."""

from benchmarks.contracts import Document, Observation
from rastro import Rastro, Recipe, Source


def extract(document: Document) -> Observation:
    mappings = [
        Recipe(**mapping.model_dump(), require_evidence=True) for mapping in document.mappings
    ]
    recipe = mappings[0].model_copy(update={"fallbacks": tuple(mappings[1:])})
    result = Rastro().extract_html(
        document.html,
        source=Source(url="https://example.com/benchmark"),
        schema=document.record_schema,
        recipe=recipe,
    )
    return Observation(
        result.status.value,
        tuple(record.data for record in result.records),
        result.metrics.inference_calls,
    )
