"""utils.helpers.format_datetime - local-time display of message dates."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from utils.helpers import format_datetime


def _local_offset_seconds() -> int:
    return -(time.altzone if time.daylight and time.localtime().tm_isdst else time.timezone)


def test_none_is_empty():
    assert format_datetime(None) == ""


def test_aware_datetime_is_converted_to_local():
    dt = datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc)
    out = format_datetime(dt)
    # The wall-clock part must match UTC + local offset.
    expected_local = dt + timedelta(seconds=_local_offset_seconds())
    assert out.startswith(expected_local.strftime("%Y-%m-%d %H:%M"))
    assert "UTC" in out  # carries an offset label


def test_naive_datetime_is_assumed_local():
    dt = datetime(2024, 3, 5, 9, 0)
    assert format_datetime(dt).startswith("2024-03-05 09:00")


def test_with_tz_false_drops_the_label():
    dt = datetime(2024, 3, 5, 9, 0)
    assert format_datetime(dt, with_tz=False) == "2024-03-05 09:00"
