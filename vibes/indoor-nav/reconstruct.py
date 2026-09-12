#!/usr/bin/env python3
"""Offline, numpy-only sensor reconstruction. Source SQLite is opened read-only."""

import argparse, csv, json, sqlite3
from pathlib import Path
import numpy as np


def rotate(q, v):
    q = q / np.linalg.norm(q, axis=1)[:, None]
    return v + 2 * np.cross(q[:, :3], np.cross(q[:, :3], v) + q[:, 3, None] * v)


def interp(t, ts, v):
    return np.column_stack([np.interp(t, ts, v[:, k]) for k in range(v.shape[1])])


def quaternions(t, ts, q):
    q = q[:, :4].copy()
    for i in range(1, len(q)):
        if q[i] @ q[i - 1] < 0:
            q[i] *= -1
    q = interp(t, ts, q)
    return q / np.linalg.norm(q, axis=1)[:, None]


def bandpass(x, fs=50, lo=0.7, hi=3):
    # Reflected FFT padding limits boundary discontinuities; smooth frequency rolloff.
    n = min(len(x) - 1, int(fs * 5))
    y = np.pad(x, ((n, n), (0, 0)), mode="reflect")
    f = np.fft.rfftfreq(len(y), 1 / fs)
    w = (1 - np.exp(-((f / lo) ** 6))) * np.exp(-((f / hi) ** 8))
    return np.fft.irfft(np.fft.rfft(y, axis=0) * w[:, None], n=len(y), axis=0)[n:-n]


def axes(t, a, centers, window):
    angles, ratios = [], []
    for c in centers:
        x = a[abs(t - c) <= window / 2, :2]
        ev, vec = np.linalg.eigh(np.cov(x.T))
        angles.append(np.arctan2(vec[1, -1], vec[0, -1]))
        ratios.append(ev[-1] / max(ev.sum(), 1e-10))
    # Axis has 180 degree ambiguity. Unwrapping imposes smoothness, not evidence of forward direction.
    return np.unwrap(2 * np.array(angles)) / 2, np.array(ratios)


def peaks(t, x, prominence=0.8):
    candidates = np.flatnonzero((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:])) + 1
    keep = []
    for i in candidates[np.argsort(x[candidates])[::-1]]:
        local = x[max(0, i - 15) : min(len(x), i + 16)]
        if x[i] - np.min(local) >= prominence and all(
            abs(t[i] - t[j]) >= 0.35 for j in keep
        ):
            keep.append(i)
    return t[sorted(keep)]


def path(step_t, centers, angle, length=0.7):
    h = np.interp(step_t, centers, angle)
    return np.vstack(
        [
            np.zeros(2),
            np.cumsum(length * np.column_stack([np.cos(h), np.sin(h)]), axis=0),
        ]
    )


def polyline(points, color, width=2, opacity=1):
    return (
        '<polyline fill="none" stroke="%s" stroke-width="%s" opacity="%s" points="%s"/>'
        % (color, width, opacity, " ".join("%.1f,%.1f" % tuple(p) for p in points))
    )


def chart(series, title, ylabel, width=940, height=230):
    xs = np.concatenate([np.asarray(s[1]) for s in series])
    ys = np.concatenate([np.asarray(s[2]) for s in series])
    xmin, xmax = xs.min(), xs.max()
    ymin, ymax = np.nanmin(ys), np.nanmax(ys)
    pad = max((ymax - ymin) * 0.08, 0.01)
    ymin -= pad
    ymax += pad
    out = f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{title}"><text x="60" y="20" class="title">{title}</text>'
    for y in np.linspace(ymin, ymax, 5):
        py = height - 35 - (y - ymin) / (ymax - ymin) * (height - 70)
        out += f'<path d="M60 {py} H{width - 20}" stroke="#dae1e9"/><text x="3" y="{py + 4}">{y:.1f}</text>'
    for x in np.linspace(xmin, xmax, 6):
        px = 60 + (x - xmin) / max(xmax - xmin, 0.01) * (width - 80)
        out += f'<text x="{px - 8}" y="{height - 13}">{x:.0f}</text>'
    for label, x, y, color in series:
        pp = np.column_stack(
            [
                60 + (np.asarray(x) - xmin) / max(xmax - xmin, 0.01) * (width - 80),
                height - 35 - (np.asarray(y) - ymin) / (ymax - ymin) * (height - 70),
            ]
        )
        valid = np.isfinite(pp).all(axis=1)
        cuts = np.flatnonzero(np.diff(np.r_[False, valid, False]))
        for a, b in zip(cuts[::2], cuts[1::2]):
            out += polyline(pp[a:b], color)
    out += (
        f'<text x="{width - 190}" y="20">{ylabel}</text></svg><div class="legend">'
        + " · ".join(f'<span style="color:{s[3]}">{s[0]}</span>' for s in series)
        + "</div>"
    )
    return out


def run(db, session, out):
    out.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    start, end = c.execute(
        "select started_at_ms,ended_at_ms from sessions where id=?", (session,)
    ).fetchone()
    duration = (end - start) / 1000
    raw = c.execute(
        "select * from sensor_samples where session_id=? order by elapsed_realtime_ns",
        (session,),
    ).fetchall()
    dense = [
        r
        for r in raw
        if r["sensor_type"] in (1, 4, 9, 10, 11, 15) and r["elapsed_realtime_ns"]
    ]
    offset = np.median(
        [r["wall_time_ms"] - r["elapsed_realtime_ns"] / 1e6 for r in dense]
    )
    audit = {}
    data = {}
    for typ in sorted(set(r["sensor_type"] for r in raw)):
        rr = [r for r in raw if r["sensor_type"] == typ]
        valid = [
            r
            for r in rr
            if r["elapsed_realtime_ns"] is not None
            and 0
            <= (r["elapsed_realtime_ns"] / 1e6 + offset - start) / 1000
            <= duration
        ]
        ts = np.array(
            [(r["elapsed_realtime_ns"] / 1e6 + offset - start) / 1000 for r in valid]
        )
        vv = np.array([json.loads(r["values_json"]) for r in valid])
        ts, ii = np.unique(ts, return_index=True)
        vv = vv[ii]
        data[typ] = (ts, vv)
        res = np.array(
            [
                r["wall_time_ms"] - r["elapsed_realtime_ns"] / 1e6 - offset
                for r in rr
                if r["elapsed_realtime_ns"]
            ]
        )
        audit[str(typ)] = {
            "rows": len(rr),
            "retained_unique": len(ts),
            "excluded": len(rr) - len(valid),
            "residual_ms_p1_p50_p99": np.percentile(res, [1, 50, 99]).tolist(),
            "max_gap_s": float(np.max(np.diff(ts))) if len(ts) > 1 else None,
        }
    # Intersection avoids extrapolation. Fail explicitly on major gaps in required streams.
    required = [1, 9, 11, 15, 4]
    lo = max(data[k][0][0] for k in required)
    hi = min(data[k][0][-1] for k in required)
    for k in required:
        if np.max(np.diff(data[k][0])) > 0.5:
            raise ValueError(f"Large gap in sensor {k}; segment stream before fusion")
    t = np.arange(lo, hi, 0.02)
    acc = interp(t, *data[1])
    grav = interp(t, *data[9])
    gyro = interp(t, *data[4])
    q = quaternions(t, *data[11])
    qgame = quaternions(t, *data[15])
    world = rotate(q, acc - grav)
    game = rotate(qgame, acc - grav)
    gworld = rotate(q, grav)
    filt = bandpass(world)
    fgame = bandpass(game)
    vertical = bandpass(
        (
            np.sum(acc * grav, axis=1) / np.linalg.norm(grav, axis=1)
            - np.linalg.norm(grav, axis=1)
        )[:, None]
    )[:, 0]
    step_t = data[18][0]
    centers = np.arange(4, duration - 3, 1.0)
    angle, ratio = axes(t, filt, centers, 6)
    ga, gr = axes(t, fgame, centers, 6)
    # Align the two frames by one constant offset, only for drift diagnosis.
    align = 0.5 * np.angle(np.mean(np.exp(2j * (angle - ga))))
    disagreement = np.degrees(0.5 * np.angle(np.exp(2j * (angle - ga - align))))
    gyro_rms = np.array(
        [np.sqrt(np.mean(np.sum(gyro[abs(t - z) < 3] ** 2, axis=1))) for z in centers]
    )
    stable = (ratio >= 0.7) & (abs(disagreement) < 20) & (gyro_rms < 1.5)
    ptime, pv = data[6]
    bt = np.arange(2, duration - 2)
    pressure = np.array([np.median(pv[abs(ptime - z) <= 2, 0]) for z in bt])
    p0 = np.median(pv[(ptime >= 5) & (ptime <= 30), 0])
    height = 8434 * np.log(p0 / pressure)
    bins = np.arange(0, duration + 10, 10)
    counts, _ = np.histogram(step_t, bins)
    accpeaks = peaks(t, vertical)
    distances = {
        str(length): float(len(step_t) * length) for length in [0.5, 0.7, 0.85]
    }
    windows = []
    for a, b, n in zip(bins[:-1], np.minimum(bins[1:], duration), counts):
        mask = (centers >= a) & (centers < b)
        windows.append(
            {
                "start_s": float(a),
                "end_s": float(b),
                "steps": int(n),
                "cadence_spm": float(n * 60 / (b - a)),
                "axis_anisotropy": float(np.median(ratio[mask]))
                if mask.any()
                else None,
                "passes_axis_quality_fraction": float(np.mean(stable[mask]))
                if mask.any()
                else None,
                "relative_height_m": float(np.interp((a + b) / 2, bt, height)),
            }
        )
    turns = []
    for i in range(5, len(centers) - 5):
        # Compare robust 3 s axes separated by 6 s; not a signed person turn.
        delta = abs(
            np.degrees(
                0.5
                * np.angle(
                    np.exp(
                        2j
                        * (
                            np.median(angle[i + 3 : i + 6])
                            - np.median(angle[i - 5 : i - 2])
                        )
                    )
                )
            )
        )
        if delta >= 40 and (not turns or centers[i] - turns[-1]["time_s"] > 8):
            turns.append(
                {
                    "time_s": float(centers[i]),
                    "axis_change_deg": float(delta),
                    "passes_quality_gate": bool(np.mean(stable[i - 5 : i + 6]) > 0.7),
                }
            )
    pauses = [
        {"from_s": float(a), "to_s": float(b), "gap_s": float(b - a)}
        for a, b in zip(step_t[:-1], step_t[1:])
        if b - a > 2
    ]
    locs = {}
    for provider in ["gps", "fused", "network"]:
        rr = c.execute(
            "select * from locations where session_id=? and provider=? order by elapsed_realtime_ns",
            (session, provider),
        ).fetchall()
        seen = set()
        ll = []
        for r in rr:
            lt = (
                (r["elapsed_realtime_ns"] / 1e6 + offset - start) / 1000
                if r["elapsed_realtime_ns"]
                else (r["wall_time_ms"] - start) / 1000
            )
            key = (r["elapsed_realtime_ns"], r["latitude"], r["longitude"])
            if 0 <= lt <= duration and key not in seen:
                seen.add(key)
                ll.append((lt, r["latitude"], r["longitude"], r["accuracy_m"]))
        if ll:
            z = np.array(ll)
            lat0, lon0 = z[0, 1:3]
            xy = np.column_stack(
                [
                    (z[:, 2] - lon0) * 111320 * np.cos(np.radians(lat0)),
                    (z[:, 1] - lat0) * 111320,
                ]
            )
            locs[provider] = {
                "rows": len(rr),
                "unique_in_session": len(ll),
                "endpoint_displacement_m": float(np.linalg.norm(xy[-1])),
                "median_accuracy_m": float(np.median(z[:, 3])),
                "wall_minus_aligned_ms_median": float(
                    np.median(
                        [
                            r["wall_time_ms"]
                            - (r["elapsed_realtime_ns"] / 1e6 + offset)
                            for r in rr
                            if r["elapsed_realtime_ns"]
                        ]
                    )
                ),
            }
    media = [
        {
            "start_s": (r["started_at_ms"] - start) / 1000,
            "end_s": (r["ended_at_ms"] - start) / 1000,
        }
        for r in c.execute("select * from media where session_id=?", (session,))
    ]
    early = [
        r["wall_time_ms"] - r["elapsed_realtime_ns"] / 1e6
        for r in dense
        if r["wall_time_ms"] < start + 30000
    ]
    late = [
        r["wall_time_ms"] - r["elapsed_realtime_ns"] / 1e6
        for r in dense
        if r["wall_time_ms"] > end - 30000
    ]
    summary = {
        "clock_offset_late_minus_early_ms": float(np.median(late) - np.median(early)),
        "accelerometer_peaks_before_first_step": int(np.sum(accpeaks < step_t[0])),
        "accelerometer_peaks_within_detector_span": int(
            np.sum((accpeaks >= step_t[0]) & (accpeaks <= step_t[-1]))
        ),
        "session": session,
        "duration_s": duration,
        "clock_offset_ms": float(offset),
        "clock_audit": audit,
        "step_events": len(step_t),
        "first_last_step_s": [float(step_t[0]), float(step_t[-1])],
        "accelerometer_peak_count": len(accpeaks),
        "accelerometer_peak_count_threshold_sensitivity": {
            str(p): len(peaks(t, vertical, p)) for p in [0.5, 0.8, 1.2]
        },
        "distance_m_by_assumed_step_length": distances,
        "gravity_world_horizontal_rms_mps2": float(
            np.sqrt(np.mean(np.sum(gworld[:, :2] ** 2, axis=1)))
        ),
        "axis_quality_fraction": float(np.mean(stable)),
        "rv_game_axis_disagreement_deg_p50_p95": np.percentile(
            abs(disagreement), [50, 95]
        ).tolist(),
        "magnetic_magnitude_uT_p5_p50_p95": np.percentile(
            np.linalg.norm(data[2][1][:, :3], axis=1), [5, 50, 95]
        ).tolist(),
        "pressure_baseline_hpa": float(p0),
        "final_height_equivalent_m": float(np.median(height[bt > duration - 12])),
        "height_at_140_s_m": float(np.interp(140, bt, height)),
        "windows": windows,
        "step_gaps_over_2s": pauses,
        "candidate_axis_changes": turns,
        "locations_diagnostic_only": locs,
        "video_metadata_only": media,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    with (out / "segments.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=windows[0])
        w.writeheader()
        w.writerows(windows)
    pp = path(step_t, centers, angle)
    with (out / "steps.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "time_s",
                "candidate_x_m",
                "candidate_y_m",
                "axis_quality_gate",
                "relative_height_equivalent_m",
            ]
        )
        w.writerows(
            (
                z,
                *p,
                bool(stable[np.argmin(abs(centers - z))]),
                float(np.interp(z, bt, height)),
            )
            for z, p in zip(step_t, pp[1:])
        )
    paths = [("6 s axis", pp, "#2166ac")]
    for win, col in [(4, "#e08214"), (8, "#8e44ad")]:
        aa, _ = axes(t, filt, centers, win)
        aa += np.pi * round((angle[0] - aa[0]) / np.pi)
        paths.append((f"{win} s axis", path(step_t, centers, aa), col))
    paths += [
        ("reversed sign", -pp, "#8a969e"),
        ("perpendicular axis", np.column_stack([-pp[:, 1], pp[:, 0]]), "#228b69"),
    ]
    xy = np.concatenate([p for _, p, _ in paths])
    mid = (xy.max(0) + xy.min(0)) / 2
    span = max(np.ptp(xy, axis=0)) * 1.15
    scale = 490 / span
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 940 590"><text x="25" y="25" class="title">Alternative sensor-only trajectories — no unique route identified</text>'
    for name, p, col in paths:
        mapped = (p - mid) * [scale, -scale] + [470, 300]
        svg += polyline(mapped, col, 2, 0.8)
        svg += f'<circle cx="{mapped[-1, 0]}" cy="{mapped[-1, 1]}" r="4" fill="{col}"/>'
    svg += f'<path d="M40 550 h{20 * scale}" stroke="#202a35" stroke-width="3"/><text x="40" y="575">20 m at assumed 0.70 m/step</text></svg>'
    html = """<!doctype html><meta charset="utf-8"><title>Navsense session reconstruction</title><style>body{font:16px system-ui;background:#eef2f6;color:#172533;max-width:1000px;margin:30px auto;padding:20px}section{background:white;padding:24px;margin:20px 0;border-radius:12px}svg{width:100%;font:12px system-ui}.title{font-size:17px;font-weight:650}.legend{font-size:13px}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}table{width:100%;border-collapse:collapse}.callout{background:#fff0d8;padding:14px}h1{font-size:30px}</style>"""
    html += (
        f'<h1>Session {session}: empirical walking reconstruction</h1><p>{duration:.1f} seconds · {len(step_t)} detected steps · sensor-only analysis</p><p class="callout">Path shape is a hypothesis. Freely held phone motion, forward/backward ambiguity, and unknown step length prevent a reliable corridor map. Colors below are sensitivity alternatives, not confidence bounds.</p><section>'
        + svg
        + '<div class="legend">'
        + " · ".join(
            f'<span style="color:{col}">{name}</span>' for name, _, col in paths
        )
        + "</div><p>All paths start at an arbitrary origin. Smooth axis sign continuity is imposed. A perpendicular alternative illustrates that dominant hand acceleration need not be forward. Absolute heading is not validated.</p></section>"
    )
    html += (
        "<section>"
        + chart(
            [
                (
                    "step detector",
                    (bins[:-1] + np.minimum(bins[1:], duration)) / 2,
                    counts * 60 / (np.minimum(bins[1:], duration) - bins[:-1]),
                    "#2166ac",
                )
            ],
            "Walking activity",
            "steps/min; time (s)",
        )
        + chart(
            [("pressure-derived height", bt, height, "#228b69")],
            "Relative elevation equivalent",
            "m; time (s)",
        )
        + "<p>Pressure is converted using an 8,434 m scale height, referenced to seconds 5–30. Height equivalent can also reflect environmental pressure; floor count, stairs and elevator are not established.</p></section>"
    )
    html += (
        "<section>"
        + chart(
            [
                ("PCA major variance share", centers, ratio, "#2166ac"),
                (
                    "passes heuristic quality gate",
                    centers,
                    stable.astype(float),
                    "#e08214",
                ),
            ],
            "Horizontal motion-axis quality",
            "0–1; time (s)",
        )
        + chart(
            [("RV / game-RV axis mismatch", centers, disagreement, "#8e44ad")],
            "Orientation sensitivity after constant alignment",
            "degrees; time (s)",
        )
        + "<p>Quality gate: major variance share ≥0.70, frame disagreement &lt;20°, gyro RMS &lt;1.5 rad/s. These thresholds are uncalibrated heuristics, not correctness probabilities. Phone swing can pass them.</p></section>"
    )
    html += "<section><h2>Ten-second evidence windows</h2><table><tr><th>Time (s)</th><th>Steps</th><th>Axis gate pass</th><th>Height equiv. (m)</th></tr>"
    for w in windows:
        quality = (
            format(w["passes_axis_quality_fraction"], ".0%")
            if w["passes_axis_quality_fraction"] is not None
            else "not evaluated"
        )
        html += f"<tr><td>{w['start_s']:.0f}–{w['end_s']:.0f}</td><td>{w['steps']}</td><td>{quality}</td><td>{w['relative_height_m']:.1f}</td></tr>"

    html += (
        "</table></section><section><h2>Method and limits</h2><p>Monotonic sensor time aligned by robust median wall-clock offset; stale events excluded; common 50 Hz grid; quaternion interpolation with sign continuity; gravity subtraction and world rotation; 0.7–3 Hz filtering; six-second horizontal PCA; step-driven displacement under fixed step length. No acceleration double integration, map matching, GPS fusion, or video interpretation.</p><p>4/6/8-second windows and axis alternatives expose model sensitivity. Step lengths 0.50/0.70/0.85 m imply "
        + "/".join(f"{x:.1f}" for x in distances.values())
        + ' m, an assumption range rather than a confidence interval. Location providers are audited separately and never treated as independent truth.</p><p><a href="summary.json">Full measurements and clock audit</a> · <a href="steps.csv">Candidate per-step coordinates</a> · <a href="segments.csv">Window measurements</a> · <a href="https://developer.android.com/reference/android/hardware/SensorEvent">Android coordinate and sensor conventions</a></p></section>'
    )
    from refine import analyze

    analyze(
        t,
        world,
        game,
        rotate(q, interp(t, *data[10])),
        step_t,
        centers,
        angle,
        stable,
        gyro,
        out,
    )
    html += '<section><a href="refined_report.html">Follow-up: step-synchronous direction and turn analysis</a></section>'
    (out / "report.html").write_text(html)
    (out / "paths.svg").write_text(svg)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ["clock_audit", "windows"]},
            indent=2,
        )
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--session", type=int, default=2)
    parser.add_argument("--out", type=Path, default=Path("output"))
    a = parser.parse_args()
    run(a.database, a.session, a.out)
