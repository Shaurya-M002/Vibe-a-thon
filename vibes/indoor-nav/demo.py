"""Create a clearly labeled synthetic SQLite recording; never overwrite a DB."""

import argparse
import json
import math
from pathlib import Path
import sqlite3

from recording_store import DEFAULT_DATABASE, SCHEMA


def build_demo(path=DEFAULT_DATABASE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing personal recording store.
    with path.open("xb"):
        pass
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        audit = {
            "gps": {
                "first_s": 0,
                "median_accuracy_m": 8,
                "max_gap_including_edges_s": 1,
            }
        }
        connection.execute(
            "INSERT INTO playback_sessions VALUES (?, ?, ?, ?, ?)",
            (
                1,
                "Synthetic demo",
                120,
                json.dumps(audit),
                "Synthetic demo — simulated motion and a 3 m rise. "
                "This is not a recorded route or a measured floor.",
            ),
        )
        rows = []
        for t in range(121):
            height = max(0, min(3, (t - 50) * 0.15))
            walking = t < 50 or t > 70
            values = {
                "height": height,
                "acceleration": 9.81 + (0.6 * math.sin(t * 1.7) if walking else 0.03),
                "linearAcceleration": 1 + 0.3 * math.sin(t) if walking else 0.04,
                "rotation": 0.15 + 0.1 * math.sin(t / 3),
                "magnetic": 42 + 4 * math.sin(t / 8),
                "heading": (350 + t) % 360,
                "light": 180 + 80 * math.cos(t / 15),
                "pressure": 950 * math.exp(-height / 8434),
            }
            rows.append(
                (
                    1,
                    "gps",
                    t,
                    12.8935 + min(t, 50) / 111320,
                    77.5795 + max(0, t - 70) / 111320,
                    8,
                )
            )
            rows.extend(
                (1, kind, t, value, None, None) for kind, value in values.items()
            )
            if walking and t % 2 == 0:
                rows.append((1, "steps", t, None, None, None))
        connection.executemany(
            "INSERT INTO playback_points VALUES (?, ?, ?, ?, ?, ?)", rows
        )
        for threshold in (1, 2, 3):
            connection.execute(
                "INSERT INTO playback_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, threshold, 50, 70, 3, 0, "simulated height change"),
            )
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(build_demo(args.output))
