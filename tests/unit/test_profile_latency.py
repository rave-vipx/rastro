"""Verify the latency profiler reports a stable structure (P-010 measurement harness)."""

from benchmarks.profile_latency import EXECUTORS, run


def test_profile_reports_ranked_hotspots_for_one_tiny_case() -> None:
    report = run(calls=1, warmup=0, cases=("static",))
    assert report["format_version"] == 1
    assert report["calls"] == 1 and report["warmup"] == 0
    assert [row["executor"] for row in report["cases"]] == list(EXECUTORS)
    for row in report["cases"]:
        assert row["case"] == "static"
        assert row["status"] == "success"
        assert row["records"] == 2
        assert row["per_call_ms"] > 0
        assert row["top"], "A profiled extraction must attribute time to functions"
        shares = [item["share"] for item in row["top"]]
        assert shares == sorted(shares, reverse=True)
        assert all(0 <= share <= 1 for share in shares)
        for item in row["top"]:
            assert item["function"].count(":") == 1
            assert item["cumulative_ms_per_call"] >= 0
            assert item["calls_per_call"] >= 0
