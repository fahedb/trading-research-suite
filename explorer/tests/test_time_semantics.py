"""The prototype must never apply a local timezone offset to broker keys.

Native keys belong to the exact MT5 wall-clock domain pinned by each dataset,
not UTC (ADR-0002).
Decoding them with UTC-based getters reproduces the literal encoded wall-clock
fields. Decoding them with local getters would silently shift every timestamp
by the viewer's own offset, so this module both pins the expected decode and
fails the build if a local-time API appears in the prototype.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

PROTOTYPE = Path(__file__).resolve().parent.parent / "prototype"
APP_JS = PROTOTYPE / "app.js"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Golden decodes. These are the literal broker wall-clock readings for keys that
# appear in the E1 fixture; the JavaScript formatter must produce exactly these.
GOLDEN = [
    (1614848700, "2021-03-04 Thu 09:05"),  # first fixture bar
    (1614960360, "2021-03-05 Fri 16:06"),  # trade entry
    (1614960540, "2021-03-05 Fri 16:09"),  # trade exit
    (1615208820, "2021-03-08 Mon 13:07"),  # last fixture bar
]

# JavaScript Date APIs that read the *host* timezone. None may appear in app.js.
FORBIDDEN = [
    r"\.getHours\s*\(",
    r"\.getMinutes\s*\(",
    r"\.getSeconds\s*\(",
    r"\.getFullYear\s*\(",
    r"\.getMonth\s*\(",
    r"\.getDate\s*\(",
    r"\.getDay\s*\(",
    r"\.getTimezoneOffset\s*\(",
    r"toLocaleString\s*\(",
    r"toLocaleDateString\s*\(",
    r"toLocaleTimeString\s*\(",
    r"Intl\.DateTimeFormat",
]


def broker_datetime(key: int) -> str:
    """Reference implementation of the prototype's decode rule."""
    d = dt.datetime.fromtimestamp(key, dt.UTC)
    return (
        f"{d.year:04d}-{d.month:02d}-{d.day:02d} "
        f"{WEEKDAYS[d.weekday()]} {d.hour:02d}:{d.minute:02d}"
    )


@pytest.mark.parametrize(("key", "expected"), GOLDEN)
def test_golden_broker_decode(key: int, expected: str) -> None:
    assert broker_datetime(key) == expected


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_app_js_uses_no_local_time_api(pattern: str) -> None:
    source = APP_JS.read_text()
    # Strip the block comment header, which names these APIs in prose.
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    matches = re.findall(pattern, body)
    assert not matches, f"local-time API {pattern!r} found in app.js: {matches}"


def test_app_js_constructs_date_only_in_the_decoder() -> None:
    """`new Date(...)` must appear exactly once, inside brokerFields()."""
    body = re.sub(r"/\*.*?\*/", "", APP_JS.read_text(), flags=re.DOTALL)
    assert body.count("new Date(") == 1, "Date construction must stay in brokerFields()"
    decoder = body.split("function brokerFields")[1].split("\n}")[0]
    assert "new Date(" in decoder


def test_app_js_uses_utc_getters() -> None:
    body = APP_JS.read_text()
    for getter in ("getUTCFullYear", "getUTCMonth", "getUTCDate",
                   "getUTCDay", "getUTCHours", "getUTCMinutes"):
        assert getter in body, f"expected UTC getter {getter} in app.js"
