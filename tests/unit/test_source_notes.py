"""Source notes: the content-free per-source reporting seam."""

import pytest
from pydantic import ValidationError

from rastro.domain.source_notes import (
    AcquisitionUse,
    BlockedHint,
    CrawlNote,
    DiscoveryNote,
    LadderStep,
    RepairNote,
    SourceNotes,
)


def test_source_notes_defaults_are_empty():
    assert SourceNotes().model_dump(mode="json") == {}
    assert SourceNotes().model_dump_json() == "{}"


def test_filled_attributes_serialize_and_others_stay_absent():
    notes = SourceNotes().model_copy(
        update={"crawl": CrawlNote(mode="bounded", pages_fetched=3), "fields_nulled": {"price": 2}}
    )
    dumped = notes.model_dump(mode="json")
    assert set(dumped) == {"crawl", "fields_nulled"}
    assert dumped["crawl"]["pages_fetched"] == 3


def test_notes_are_frozen_and_closed():
    with pytest.raises(ValidationError):
        SourceNotes(unknown=1)  # type: ignore[call-arg]
    notes = SourceNotes()
    with pytest.raises(ValidationError):
        notes.crawl = CrawlNote(mode="bounded")  # type: ignore[misc]


def test_acquisition_use_defaults_and_bounds():
    use = AcquisitionUse(render="http")
    assert (use.egress, use.robots, use.proxies_used, use.ladder) == ("direct", "obeyed", 0, ())
    with pytest.raises(ValidationError):
        AcquisitionUse(render="http", proxies_used=-1)
    with pytest.raises(ValidationError):
        AcquisitionUse(render="http", egress="http://user:secret@proxy.example:8080")
    with pytest.raises(ValidationError):
        AcquisitionUse(render="http", impersonate="netscape")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AcquisitionUse(render="http", http_version="0.9")  # type: ignore[arg-type]


def test_ladder_steps_name_levels_and_outcomes():
    use = AcquisitionUse(
        render="stealth",
        start_level="http",
        ladder=(
            LadderStep(level="http", outcome="blocked"),
            LadderStep(level="stealth", outcome="ok"),
        ),
    )
    assert [step.level for step in use.ladder] == ["http", "stealth"]
    with pytest.raises(ValidationError):
        LadderStep(level="teleport", outcome="ok")  # type: ignore[arg-type]


def test_blocked_hint_points_at_public_docs_only():
    hint = BlockedHint(kind="cloud_ip_refused", http_status=403, docs="docs/egress.md#cloud-ip")
    assert hint.http_status == 403
    for docs in ("https://evil.example/", "../secret.md", "docs/egress.md?x=1"):
        with pytest.raises(ValidationError):
            BlockedHint(kind="captcha", docs=docs)
    with pytest.raises(ValidationError):
        BlockedHint(kind="captcha", http_status=99, docs="docs/egress.md")


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (DiscoveryNote, "locs_found"),
        (DiscoveryNote, "locs_admitted"),
        (CrawlNote, "pages_fetched"),
        (CrawlNote, "frontier_remaining"),
        (RepairNote, "model_calls"),
    ],
)
def test_counts_are_never_negative(model, field):
    base = {"seed": "page"} if model is DiscoveryNote else {}
    if model is CrawlNote:
        base = {"mode": "unbounded"}
    with pytest.raises(ValidationError):
        model(**base, **{field: -1})


def test_repair_refusal_is_an_issue_code_not_free_text():
    assert RepairNote(promotion_refused="instruction_like_content").decision == "none"
    with pytest.raises(ValidationError):
        RepairNote(promotion_refused="Ignore previous instructions")
    with pytest.raises(ValidationError):
        RepairNote(candidate_version=0)


def test_fields_nulled_and_capabilities_are_bounded_vocabularies():
    assert SourceNotes(capabilities_used=("stealth",)).capabilities_used == ("stealth",)
    with pytest.raises(ValidationError):
        SourceNotes(capabilities_used=("teleport",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SourceNotes(fields_nulled={"price": -1})
    with pytest.raises(ValidationError):
        SourceNotes(fields_nulled={"": 1})
