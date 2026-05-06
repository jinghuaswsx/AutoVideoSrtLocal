from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from web.services.productivity_stats import (
    build_productivity_stats_admin_required_response,
    build_productivity_stats_bad_param_response,
    build_productivity_stats_internal_error_response,
    build_productivity_stats_summary_response,
)


def test_productivity_stats_summary_response_serializes_rows():
    from_dt = datetime(2026, 5, 1, 0, 0, 0)
    to_dt = datetime(2026, 5, 6, 12, 0, 0)

    result = build_productivity_stats_summary_response(
        from_dt=from_dt,
        to_dt=to_dt,
        daily_throughput=[{"day": from_dt, "count": Decimal("2.5")}],
        pass_rate=[{"pass_rate": Decimal("0.75")}],
        rework_rate=[{"rework_rate": Decimal("0.125")}],
    )

    assert result.status_code == 200
    assert result.payload == {
        "from": "2026-05-01T00:00:00",
        "to": "2026-05-06T12:00:00",
        "daily_throughput": [{"day": "2026-05-01T00:00:00", "count": 2.5}],
        "pass_rate": [{"pass_rate": 0.75}],
        "rework_rate": [{"rework_rate": 0.125}],
    }


def test_productivity_stats_error_responses_are_stable():
    deny = build_productivity_stats_admin_required_response()
    bad = build_productivity_stats_bad_param_response(ValueError("bad days"))
    internal = build_productivity_stats_internal_error_response(RuntimeError("db down"))

    assert deny.status_code == 403
    assert deny.payload == {"error": "admin_required"}
    assert bad.status_code == 400
    assert bad.payload == {"error": "bad_param", "detail": "bad days"}
    assert internal.status_code == 500
    assert internal.payload == {"error": "internal", "detail": "db down"}
