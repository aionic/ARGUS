import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profiling import build_profile_report  # noqa: E402


def test_build_profile_report_aggregates_successes_failures_and_quality():
    runs = [
        {
            "tier": "economy",
            "success": True,
            "usd_per_page": 0.10,
            "total_tokens": 100,
            "quality_bucket": "ok",
        },
        {
            "tier": "economy",
            "success": False,
            "failure_type": "ValueError",
            "usd_per_page": 0.50,
            "total_tokens": 500,
            "quality_bucket": "flagged",
        },
        {
            "tier": "premium",
            "success": True,
            "usd_per_page": 0.30,
            "total_tokens": 300,
            "quality_bucket": "ok",
        },
    ]

    report = build_profile_report(runs, ["economy", "premium"], generated_at="2026-06-24T00:00:00+00:00")

    assert report["per_tier"]["economy"] == {
        "avg_usd_per_page": 0.1,
        "avg_tokens": 100.0,
        "success_rate": 0.5,
        "failure_types": {"ValueError": 1},
        "quality_dist": {"flagged": 1, "ok": 1},
    }
    assert report["per_tier"]["premium"]["avg_usd_per_page"] == 0.3
    assert report["per_tier"]["premium"]["success_rate"] == 1.0
    assert report["generated_at"] == "2026-06-24T00:00:00+00:00"
