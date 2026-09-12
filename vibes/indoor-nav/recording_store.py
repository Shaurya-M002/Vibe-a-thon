"""Read-only access to the portal playback schema, independent of HTTP."""

import json
import sqlite3
from pathlib import Path

DEFAULT_DATABASE = Path(__file__).resolve().parent / "output" / "recordings.sqlite"
SCHEMA = """
CREATE TABLE playback_sessions (
    id INTEGER PRIMARY KEY, start TEXT, duration REAL, audit TEXT, story TEXT
);
CREATE TABLE playback_points (
    session_id INTEGER REFERENCES playback_sessions(id), kind TEXT,
    t REAL, v1 REAL, v2 REAL, v3 REAL
);
CREATE INDEX playback_points_session ON playback_points(session_id, kind, t);
CREATE TABLE playback_events (
    session_id INTEGER REFERENCES playback_sessions(id), threshold INTEGER,
    start REAL, end REAL, delta REAL, steps INTEGER, kind TEXT
);
"""


def recordings(database=DEFAULT_DATABASE):
    """Return chronological playback records without creating or modifying a DB."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    result = {"sessions": []}
    with sqlite3.connect(uri, uri=True) as connection:
        for sid, start, duration, audit, story in connection.execute(
            "SELECT id, start, duration, audit, story FROM playback_sessions ORDER BY id"
        ):
            series = {}
            for kind, time, first, second, third in connection.execute(
                "SELECT kind, t, v1, v2, v3 FROM playback_points "
                "WHERE session_id = ? ORDER BY t",
                (sid,),
            ):
                values = [first, second, third] if kind == "gps" else [first]
                series.setdefault(kind, []).append([time, *values])
            events = {}
            for threshold in (1, 2, 3):
                rows = connection.execute(
                    "SELECT start, end, delta, steps, kind FROM playback_events "
                    "WHERE session_id = ? AND threshold = ? ORDER BY start",
                    (sid, threshold),
                )
                events[str(threshold)] = [
                    dict(zip(("start", "end", "delta", "steps", "kind"), row))
                    for row in rows
                ]
            result["sessions"].append(
                {
                    "id": sid,
                    "start": start,
                    "duration": duration,
                    "audit": json.loads(audit),
                    "story": story,
                    "gps": series.pop("gps", []),
                    "height": series.pop("height", []),
                    "steps": [point[0] for point in series.pop("steps", [])],
                    "radios": [
                        {"type": kind, "bins": series.pop(kind, [])}
                        for kind in ("wifi", "bluetooth_le")
                    ],
                    "events": events,
                    "sensors": series,
                }
            )
    return result
