"""Build the portal's single SQLite store; source exports are opened read-only."""

import sqlite3, json, os, statistics, math
import numpy as np
from dashboard.analyze_new import vertical_candidates
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "output/recordings.sqlite"
LATEST = ROOT / "output/dataset_01a096d5/navsense.sqlite"
ORIGINAL = ROOT / "data/original/navsense.sqlite"


def build(original=None, latest=None, target=None, analysis_dir=ROOT / "data/analysis"):
    ORIGINAL = Path(original or ROOT / "data/original/navsense.sqlite")
    LATEST = Path(latest or ROOT / "output/dataset_01a096d5/navsense.sqlite")
    TARGET = Path(target or ROOT / "output/recordings.sqlite")
    inputs = [
        ORIGINAL,
        LATEST,
        analysis_dir / "model-data.json",
        analysis_dir / "new_data.json",
        analysis_dir / "latest_data.json",
    ]
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(f"Required local input missing: {path}")
    if TARGET.resolve() in [p.resolve() for p in inputs]:
        raise ValueError("Output must not replace an input")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".building.sqlite")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    src = sqlite3.connect(f"file:{LATEST}?mode=ro", uri=True)
    src.backup(db)
    src.close()
    tables = [
        r[0]
        for r in db.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )
    ]
    db.execute("PRAGMA foreign_keys=OFF")
    for table in tables:
        cols = [r[1] for r in db.execute(f"pragma table_info({table})")]
        if "session_id" in cols:
            db.execute(f"update {table} set session_id=session_id+2")
    db.execute("update sessions set id=-id")
    db.execute("update sessions set id=-id+2")
    db.execute(
        "create table recording_sources(session_id integer primary key, source_export text, source_session_id integer)"
    )
    db.executemany(
        "insert into recording_sources values(?,?,?)",
        [(i + 2, LATEST.parent.name, i) for i in range(1, 10)],
    )
    old = sqlite3.connect(f"file:{ORIGINAL}?mode=ro", uri=True)
    for table in tables:
        cols = [r[1] for r in db.execute(f"pragma table_info({table})")]
        if table != "sessions" and "session_id" not in cols:
            continue
        field = "id" if table == "sessions" else "session_id"
        for row in old.execute(f"select * from {table} where {field} in (2,3)"):
            record = dict(
                zip([r[1] for r in old.execute(f"pragma table_info({table})")], row)
            )
            record[field] -= 1
            if table != "sessions":
                record.pop("id", None)
            keys = [k for k in record if k in cols]
            db.execute(
                f"insert into {table} ({','.join(keys)}) values ({','.join('?' for _ in keys)})",
                [record[k] for k in keys],
            )
    db.executemany(
        "insert into recording_sources values(?,?,?)",
        [(i - 1, ORIGINAL.parent.name, i) for i in [2, 3]],
    )
    old.close()
    db.executescript("""CREATE TABLE playback_sessions(id INTEGER PRIMARY KEY, start TEXT, duration REAL, audit TEXT, story TEXT);
 CREATE TABLE playback_points(session_id INTEGER,kind TEXT,t REAL,v1 REAL,v2 REAL,v3 REAL);
 CREATE INDEX playback_points_session ON playback_points(session_id,kind,t);
 CREATE TABLE playback_events(session_id INTEGER,threshold INTEGER,start REAL,end REAL,delta REAL,steps INTEGER,kind TEXT);""")
    original = json.loads((analysis_dir / "model-data.json").read_text())
    sessions = []
    for key, r in original["sessions"].items():
        sid = int(key) - 1
        lat, lon = original["origin"]
        gps = [
            [
                p[0],
                lat - p[2] / 111320,
                lon + p[1] / (111320 * math.cos(math.radians(lat))),
                p[3],
            ]
            for p in r["gps"]
        ]
        sessions.append(
            dict(
                id=sid,
                start="",
                duration=r["duration"],
                gps=gps,
                height=r["height"],
                steps=[p[0] if isinstance(p, list) else p for p in r["steps"]],
                radios=[],
                events={},
                audit={"gps": {}},
            )
        )
    for file in ["new_data.json", "latest_data.json"]:
        for r in json.loads((analysis_dir / file).read_text())["sessions"]:
            r["id"] += 2
            sessions.append(r)
    from datetime import datetime, timezone, timedelta

    for r in sorted(sessions, key=lambda r: r["id"]):
        sid = r["id"]
        start = db.execute(
            "select started_at_ms from sessions where id=?", (sid,)
        ).fetchone()[0]
        date = datetime.fromtimestamp(
            start / 1000, timezone(timedelta(hours=5.5))
        ).strftime("%d %b · %H:%M")
        gps = r["gps"]
        gaps = (
            (
                [gps[0][0]]
                + [b[0] - a[0] for a, b in zip(gps, gps[1:])]
                + [r["duration"] - gps[-1][0]]
            )
            if gps
            else []
        )
        r["audit"]["gps"] = (
            {
                "first_s": gps[0][0],
                "median_accuracy_m": round(statistics.median(p[3] for p in gps), 2),
                "max_gap_including_edges_s": round(max(gaps), 1),
            }
            if gps
            else {}
        )
        if not r["events"]:
            ht = np.array([p[0] for p in r["height"]])
            hv = np.array([p[1] for p in r["height"]])
            steps = np.array(r["steps"])
            r["events"] = {
                str(th): vertical_candidates(ht, hv, steps, th) if len(ht) > 15 else []
                for th in [1, 2, 3]
            }
        if not r["radios"]:
            for radio in ["wifi", "bluetooth_le"]:
                rbins = {}
                for wall, key in db.execute(
                    "select wall_time_ms,identifier from radio_observations where session_id=? and radio=?",
                    (sid, radio),
                ):
                    sec = (wall - start) / 1000
                    if 0 <= sec <= r["duration"]:
                        rbins.setdefault(int(sec // 10) * 10, set()).add(key)
                r["radios"].append(
                    {
                        "type": radio,
                        "bins": [[t, len(keys)] for t, keys in sorted(rbins.items())],
                    }
                )
        story = (
            "You tentatively recalled a one-floor lift ride here; it remains unconfirmed."
            if sid == 9
            else ""
        )
        db.execute(
            "insert into playback_sessions values(?,?,?,?,?)",
            (sid, date, r["duration"], json.dumps(r["audit"]), story),
        )

        def points(kind, rows):
            db.executemany(
                "insert into playback_points values(?,?,?,?,?,?)",
                [(sid, kind, *list(p), *([None] * (4 - len(p)))) for p in rows],
            )

        points("gps", r["gps"])
        points("height", r["height"])
        points("steps", [[v] for v in r["steps"]])
        for radio in r["radios"]:
            points(radio["type"], radio["bins"])
        for th, events in r["events"].items():
            for e in events:
                db.execute(
                    "insert into playback_events values(?,?,?,?,?,?,?)",
                    (
                        sid,
                        int(th),
                        e["start"],
                        e["end"],
                        e["delta"],
                        e["steps"],
                        e["kind"],
                    ),
                )
        # Aggregate raw measured pressure and acceleration magnitude into one-second bins.
        offset = db.execute(
            "select avg(wall_time_ms-elapsed_realtime_ns/1000000.0) from sensor_samples where session_id=? and sensor_type=1 and elapsed_realtime_ns>0",
            (sid,),
        ).fetchone()[0]
        bins = {}
        if offset is not None:
            for kind, ns, values in db.execute(
                "select sensor_type,elapsed_realtime_ns,values_json from sensor_samples where session_id=? and sensor_type in (1,2,3,4,5,6,10) and elapsed_realtime_ns>0",
                (sid,),
            ):
                t = (ns / 1e6 + offset - start) / 1000
                if 0 <= t <= r["duration"]:
                    v = json.loads(values)
                    value = (
                        v[0]
                        if kind in (3, 5, 6)
                        else math.sqrt(sum(x * x for x in v[:3]))
                    )
                    bins.setdefault((kind, int(t)), []).append(value)
        for kind, name in [
            (1, "acceleration"),
            (2, "magnetic"),
            (3, "heading"),
            (4, "rotation"),
            (5, "light"),
            (6, "pressure"),
            (10, "linearAcceleration"),
        ]:
            points(
                name,
                [
                    [
                        t,
                        (
                            math.degrees(
                                math.atan2(
                                    sum(math.sin(math.radians(x)) for x in v),
                                    sum(math.cos(math.radians(x)) for x in v),
                                )
                            )
                            % 360
                            if kind == 3
                            else statistics.median(v)
                        ),
                    ]
                    for (k, t), v in sorted(bins.items())
                    if k == kind
                ],
            )
    db.commit()
    assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert not db.execute("pragma foreign_key_check").fetchall()
    assert [
        r[0] for r in db.execute("select id from playback_sessions order by id")
    ] == list(range(1, 12))
    db.close()
    os.replace(tmp, TARGET)
    print("Built 11 chronological recordings in", TARGET)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=TARGET)
    args = parser.parse_args()
    build(args.original, args.latest, args.output, args.analysis_dir)
