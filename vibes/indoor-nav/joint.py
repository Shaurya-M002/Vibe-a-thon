#!/usr/bin/env python3
"""Cross-session reciprocal-sequence diagnostics. No mirrored-route constraint."""

import argparse, csv, json, sqlite3
from pathlib import Path
import numpy as np
from reconstruct import interp, chart


def dtw(cost, band=0.3, penalty=0.15):
    """Symmetric weighted DTW, local speed ratio bounded to 1/2..2.
    Full-trace endpoint alignment tests a hypothesis; it does not establish shared endpoints.
    """
    n, m = cost.shape
    d = np.full((n + 1, m + 1), np.inf)
    d[0, 0] = 0
    parent = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if abs((i - 1) / max(n - 1, 1) - (j - 1) / max(m - 1, 1)) > band:
                continue
            choices = [(d[i - 1, j - 1] + 2 * cost[i - 1, j - 1], i - 1, j - 1)]
            if i >= 2:
                choices.append(
                    (
                        d[i - 2, j - 1]
                        + cost[i - 2, j - 1]
                        + 2 * cost[i - 1, j - 1]
                        + penalty,
                        i - 2,
                        j - 1,
                    )
                )
            if j >= 2:
                choices.append(
                    (
                        d[i - 1, j - 2]
                        + cost[i - 1, j - 2]
                        + 2 * cost[i - 1, j - 1]
                        + penalty,
                        i - 1,
                        j - 2,
                    )
                )
            val, pi, pj = min(choices)
            d[i, j] = val
            parent[i, j] = (pi, pj)
    i, j = n, m
    p = []
    while i > 0 and j > 0:
        p.append((i - 1, j - 1))
        pi, pj = parent[i, j]
        if i - pi == 2:
            p.append((i - 2, j - 1))
        if j - pj == 2:
            p.append((i - 1, j - 2))
        i, j = pi, pj
    return float(d[n, m] / (n + m)), np.array(p[::-1])


def sensor_features(c, sid):
    start, end = c.execute(
        "select started_at_ms,ended_at_ms from sessions where id=?", (sid,)
    ).fetchone()
    dur = (end - start) / 1000
    rows = c.execute(
        "select wall_time_ms,elapsed_realtime_ns,sensor_type,values_json from sensor_samples where session_id=? and sensor_type in (1,2,6,9)",
        (sid,),
    ).fetchall()
    off = np.median([w - n / 1e6 for w, n, k, v in rows if k == 1])
    data = {}
    for typ in [2, 6, 9]:
        rr = sorted(
            [
                ((n / 1e6 + off - start) / 1000, json.loads(v))
                for w, n, k, v in rows
                if k == typ and 0 <= (n / 1e6 + off - start) / 1000 <= dur
            ]
        )
        ts = np.array([r[0] for r in rr])
        vs = np.array([r[1] for r in rr])
        ts, ix = np.unique(ts, return_index=True)
        data[typ] = (ts, vs[ix])
    mt, m = data[2]
    g = interp(mt, *data[9])
    g /= np.linalg.norm(g, axis=1)[:, None]
    vertical = np.sum(m[:, :3] * g, axis=1)
    norm = np.linalg.norm(m[:, :3], axis=1)
    horizontal = np.sqrt(np.maximum(norm**2 - vertical**2, 0))
    ts = np.arange(2, dur - 2, 2.0)
    features = []
    for t in ts:
        mask = abs(mt - t) <= 1
        p = np.median(data[6][1][abs(data[6][0] - t) <= 1, 0])
        features.append(
            [
                p,
                np.median(norm[mask]),
                np.median(vertical[mask]),
                np.median(horizontal[mask]),
            ]
        )
    return start, dur, ts, np.array(features)


def radio_bins(c, sid, start, dur, radio, size=10):
    groups = {}
    seen = set()
    raw = 0
    for wall, ident, rssi in c.execute(
        "select wall_time_ms,identifier,rssi from radio_observations where session_id=? and radio=?",
        (sid, radio),
    ):
        raw += 1
        t = (wall - start) / 1000
        if ident is None or rssi is None or not 0 <= t < dur:
            continue
        # Multiple identical callback values in the same 1-second bucket count once.
        key = (int(t), ident, rssi)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault(int(t // size), {}).setdefault(ident, []).append(rssi)
    ids = sorted(groups)
    return (
        np.array([(k * size + min((k + 1) * size, dur)) / 2 for k in ids]),
        [{i: float(np.median(v)) for i, v in groups[k].items()} for k in ids],
        {
            "rows": raw,
            "unique_second_identifier_rssi": len(seen),
            "populated_bins": len(ids),
            "unique_identifiers": len(set(i for g in groups.values() for i in g)),
        },
    )


def similarity(a, b):
    keys = set(a) | set(b)
    # Presence weighted by observed RSSI, bounded to avoid domination by one transmitter.
    wa = {k: np.clip((a.get(k, -100) + 100) / 50, 0, 1) for k in keys}
    wb = {k: np.clip((b.get(k, -100) + 100) / 50, 0, 1) for k in keys}
    return sum(min(wa[k], wb[k]) for k in keys) / max(
        sum(max(wa[k], wb[k]) for k in keys), 1e-9
    )


def heatmap(matrix, title, xt, yt, path=None):
    rows, cols = matrix.shape
    w, h = 900, 420
    out = (
        f'<svg viewBox="0 0 1000 510"><text x="65" y="25" class="title">{title}</text>'
    )
    for i in range(rows):
        for j in range(cols):
            v = float(np.clip(matrix[i, j], 0, 1))
            color = (
                f"rgb({int(245 - 220 * v)},{int(248 - 125 * v)},{int(252 - 92 * v)})"
            )
            out += f'<rect x="{65 + j * w / cols:.2f}" y="{45 + i * h / rows:.2f}" width="{w / cols + 0.2:.2f}" height="{h / rows + 0.2:.2f}" fill="{color}"/>'
    for ix in np.linspace(0, cols - 1, 6).astype(int):
        out += f'<text x="{65 + (ix + 0.5) * w / cols}" y="487">{xt[ix]:.0f}</text>'
    for ix in np.linspace(0, rows - 1, 6).astype(int):
        out += f'<text x="7" y="{45 + (ix + 0.5) * h / rows}">{yt[ix]:.0f}</text>'
    out += '<text x="450" y="505">Session 3 time (s); rows: session 2 time (s)</text>'
    return out + "</svg>"


def elevation_segments(t, h, max_segments=4):
    # BIC-selected piecewise linear trend; 12-second minimum segment, 0.2 m noise floor.
    n = len(t)
    minimum = 6
    rss = np.full((n, n + 1), np.inf)
    coef = {}
    for i in range(n):
        for j in range(i + minimum, n + 1):
            x = np.column_stack([t[i:j], np.ones(j - i)])
            b = np.linalg.lstsq(x, h[i:j], rcond=None)[0]
            rss[i, j] = np.sum((h[i:j] - x @ b) ** 2)
            coef[i, j] = b
    dp = np.full((max_segments + 1, n + 1), np.inf)
    dp[0, 0] = 0
    parents = {}
    options = []
    for k in range(1, max_segments + 1):
        for j in range(k * minimum, n + 1):
            choices = np.array(
                [
                    dp[k - 1, i] + rss[i, j]
                    for i in range((k - 1) * minimum, j - minimum + 1)
                ]
            )
            ix = int(np.argmin(choices)) + (k - 1) * minimum
            dp[k, j] = dp[k - 1, ix] + rss[ix, j]
            parents[k, j] = ix
        bic = n * np.log(max(dp[k, n] / n, 0.2**2)) + (3 * k - 1) * np.log(n)
        options.append(float(bic))
    k = int(np.argmin(options)) + 1
    j = n
    segments = []
    while k:
        i = parents[k, j]
        b = coef[i, j]
        segments.append(
            {
                "start_s": float(t[i]),
                "end_s": float(t[j - 1]),
                "slope_mps": float(b[0]),
                "fitted_change_m": float(b[0] * (t[j - 1] - t[i])),
                "classification": "rising pressure-height"
                if b[0] > 0.05
                else (
                    "falling pressure-height"
                    if b[0] < -0.05
                    else "approximately level pressure-height"
                ),
            }
        )
        j = i
        k -= 1
    return {
        "segments": segments[::-1],
        "bic_by_1_to_4_segments": options,
        "noise_floor_m": 0.2,
        "minimum_segment_s": 12,
    }


def run(db, out):
    c = sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True)
    out.mkdir(parents=True, exist_ok=True)
    start2, d2, t2, f2 = sensor_features(c, 2)
    start3, d3, t3, f3 = sensor_features(c, 3)
    p0 = np.median(f2[t2 <= 20, 0])
    h2 = 8434 * np.log(p0 / f2[:, 0])
    h3 = 8434 * np.log(p0 / f3[:, 0])
    results = {}
    alignments = {}
    # Fixed physical scales keep comparators honest; do not independently normalize each trace.
    feature_sets = {
        "pressure": ([0], [0.15]),
        "magnetic_norm": ([1], [8]),
        "magnetic_components": ([2, 3], [8, 8]),
        "pressure_magnetic": ([0, 2, 3], [0.15, 8, 8]),
    }
    rng = np.random.default_rng(28)
    for name, (cols, scales) in feature_sets.items():
        a = f2[:, cols] / scales
        b = f3[:, cols] / scales
        for reverse in [False, True]:
            bb = b[::-1] if reverse else b
            cost = np.sqrt(np.mean((a[:, None] - bb[None, :]) ** 2, axis=2))
            score, p = dtw(cost)
            key = name + ("_reverse" if reverse else "_forward")
            results[key] = score
            alignments[key] = p
        # Circular-shift controls preserve trace autocorrelation; descriptive, not a p-value.
        controls = []
        for shift in np.linspace(0.2 * len(b), 0.8 * len(b), 15).astype(int):
            bb = np.roll(b[::-1], shift, axis=0)
            controls.append(
                dtw(np.sqrt(np.mean((a[:, None] - bb[None, :]) ** 2, axis=2)))[0]
            )
        results[name + "_shift_control_median"] = float(np.median(controls))
        results[name + "_reverse_better_than_shift_fraction"] = float(
            np.mean(np.array(controls) > results[name + "_reverse"])
        )
    sensitivity = {}
    for band in [0.15, 0.3, 0.5]:
        for scale in [0.08, 0.15, 0.3]:
            a = f2[:, [0, 2, 3]] / [scale, 8, 8]
            b = f3[:, [0, 2, 3]] / [scale, 8, 8]
            scores = [
                dtw(
                    np.sqrt(np.mean((a[:, None] - bb[None, :]) ** 2, axis=2)), band=band
                )[0]
                for bb in [b, b[::-1]]
            ]
            sensitivity[f"band={band},pressure_scale={scale}"] = {
                "forward": scores[0],
                "reverse": scores[1],
            }
    trim_checks = {}
    for trim in [0, 5, 10]:
        ma = (t2 >= trim) & (t2 <= d2 - trim)
        mb = (t3 >= trim) & (t3 <= d3 - trim)
        a = f2[ma][:, [0, 2, 3]] / [0.15, 8, 8]
        b = f3[mb][:, [0, 2, 3]] / [0.15, 8, 8]
        scores = [
            dtw(np.sqrt(np.mean((a[:, None] - bb[None, :]) ** 2, axis=2)))[0]
            for bb in [b, b[::-1]]
        ]
        trim_checks[str(trim)] = {"forward": scores[0], "reverse": scores[1]}
    radios = {}
    radio_charts = ""
    for radio in ["wifi", "bluetooth_le", "bluetooth_classic"]:
        rt2, r2, audit2 = radio_bins(c, 2, start2, d2, radio)
        rt3, r3, audit3 = radio_bins(c, 3, start3, d3, radio)
        sim = np.array([[similarity(a, b) for b in r3] for a in r2])
        forward = []
        reverse = []
        for i, z in enumerate(rt2):
            forward.append(sim[i, np.argmin(abs(rt3 - z / d2 * d3))])
            reverse.append(sim[i, np.argmin(abs(rt3 - (1 - z / d2) * d3))])
        best = np.argmax(sim, axis=1)
        nearest = [
            {
                "s2_center_s": float(rt2[i]),
                "s3_best_center_s": float(rt3[j]),
                "weighted_jaccard": float(sim[i, j]),
            }
            for i, j in enumerate(best)
        ]
        p = alignments["pressure_magnetic_reverse"]
        matched = []
        for z in rt2:
            # Median S3 time over the fitted sensor correspondence; radio is held out.
            i = np.argmin(abs(t2 - z))
            js = p[p[:, 0] == i, 1]
            pred = np.median(t3[::-1][js])
            j = np.argmin(abs(rt3 - pred))
            matched.append(sim[np.argmin(abs(rt2 - z)), j])
        radios[radio] = {
            "session2": audit2,
            "session3": audit3,
            "forward_normalized_time_mean_similarity": float(np.mean(forward)),
            "reverse_normalized_time_mean_similarity": float(np.mean(reverse)),
            "sensor_reverse_dtw_heldout_mean_similarity": float(np.mean(matched)),
            "nearest_fingerprint_matches": nearest,
            "distinct_cross_session_shared_identifiers": len(
                set(k for a in r2 for k in a) & set(k for a in r3 for k in a)
            ),
        }
        bin_sensitivity = {}
        for size in [5, 10, 20]:
            at, af, _ = radio_bins(c, 2, start2, d2, radio, size)
            bt, bf, _ = radio_bins(c, 3, start3, d3, radio, size)
            scores = []
            for reverse in [False, True]:
                ss = []
                for z, a in zip(at, af):
                    target = (1 - z / d2) * d3 if reverse else z / d2 * d3
                    ss.append(similarity(a, bf[np.argmin(abs(bt - target))]))
                scores.append(float(np.mean(ss)))
            bin_sensitivity[str(size)] = {"forward": scores[0], "reverse": scores[1]}
        radios[radio]["bin_width_sensitivity_s"] = bin_sensitivity
        radio_charts += heatmap(
            sim, f"{radio}: fingerprint similarity (darker = stronger)", rt3, rt2
        )
    summary = {
        "assumption": "User says sessions 2/3 are outward/return. Chronological labels inferred; identical route/endpoints are not imposed.",
        "endpoint_trim_sensitivity_s": trim_checks,
        "gap_between_sessions_s": (start3 - (start2 + d2 * 1000)) / 1000,
        "height_common_reference": {
            "session2_first_10s_m": float(np.median(h2[t2 <= 10])),
            "session2_last_10s_m": float(np.median(h2[t2 >= d2 - 10])),
            "session3_first_10s_m": float(np.median(h3[t3 <= 10])),
            "session3_last_10s_m": float(np.median(h3[t3 >= d3 - 10])),
        },
        "dtw_scores_lower_better": results,
        "joint_dtw_sensitivity": sensitivity,
        "radio": radios,
        "elevation_segments": {
            "session2": elevation_segments(t2, h2),
            "session3": elevation_segments(t3, h3),
        },
    }
    (out / "joint_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False)
    )
    p = alignments["pressure_magnetic_reverse"]
    landmarks = []
    for a in range(0, 180, 30):
        mask = (t2[p[:, 0]] >= a) & (t2[p[:, 0]] < a + 30)
        matched = t3[::-1][p[mask, 1]]
        landmarks.append(
            {
                "session2_interval_s": [a, a + 30],
                "session3_fitted_interval_s": [
                    float(matched.min()),
                    float(matched.max()),
                ],
            }
        )
    summary["coarse_candidate_correspondences"] = landmarks
    (out / "joint_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False)
    )
    with (out / "reciprocal_alignment.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(["session2_time_s", "session3_time_s", "hypothesis"])
        w.writerows(
            (t2[i], t3[::-1][j], "reverse_DTW_candidate_not_ground_truth") for i, j in p
        )
    style = "<style>body{font:16px system-ui;max-width:1050px;margin:30px auto;padding:20px;background:#edf2f6;color:#182a38}section{background:white;margin:20px 0;padding:25px;border-radius:12px}svg{width:100%;font:12px system-ui}.title{font-size:17px;font-weight:650}.legend{font-size:13px}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}table{width:100%;border-collapse:collapse}</style>"
    html = (
        '<!doctype html><meta charset="utf-8"><title>Outbound / return evidence</title>'
        + style
        + "<h1>Sessions 2 + 3: reciprocal-route evidence</h1><p>The user identifies an outward and a return trip. Session 2 outward / session 3 return is a chronological inference. Shared endpoints, identical corridors, and heading are not assumed as facts.</p><section>"
        + chart(
            [("session 2", t2, h2, "#2166ac"), ("session 3", t3, h3, "#cc6611")],
            "Pressure-derived elevation, common reference",
            "m; session time (s)",
        )
        + chart(
            [
                ("session 2", t2, h2, "#2166ac"),
                ("session 3 played backward", d3 - t3[::-1], h3[::-1], "#228b69"),
            ],
            "Reciprocal elevation comparison (no warping)",
            "m; reversed time (s)",
        )
        + "<p>Height equivalent assumes pressure variation is altitude-related. Common reference preserves endpoint differences. Sensor baselines are not independently shifted to enforce closure.</p></section>"
    )
    html += "<section><h2>Sequence matching with controls</h2><table><tr><th>Feature</th><th>Forward cost</th><th>Reverse cost</th><th>Shift-control median</th></tr>"
    for name in feature_sets:
        html += f"<tr><td>{name}</td><td>{results[name + '_forward']:.3f}</td><td>{results[name + '_reverse']:.3f}</td><td>{results[name + '_shift_control_median']:.3f}</td></tr>"
    html += "</table><p>Lower cost means greater similarity. Symmetric-weight DTW with normalized-progress band 0.30, local speed ratio restricted to 0.5–2, and 0.15 stretch penalty. Fixed scales: 0.15 hPa, 8 µT. Forward and reverse hypotheses are compared. Each full-trace DTW fit aligns its endpoints by construction; this does not establish physical endpoint identity. Trimming 0/5/10 seconds from each trace tests sensitivity to recording boundaries. Circular shifts preserve autocorrelation but are only descriptive controls. A fitted reverse alignment is a candidate correspondence, not proof of identical paths.</p></section>"
    html += "<section><h2>Candidate progress sequence</h2><table><tr><th>Session 2 interval</th><th>Fitted session 3 interval (reverse order)</th></tr>"
    for z in landmarks:
        html += (
            "<tr><td>"
            + str(z["session2_interval_s"])
            + "</td><td>"
            + str(z["session3_fitted_interval_s"])
            + "</td></tr>"
        )
    html += "</table><p>Intervals are outputs of pressure/magnetic DTW. Local correspondences are not validated at metre or doorway precision. In particular, early session-2 radio fingerprints best match session 3 near 155 seconds rather than its very end, so boundary correspondence is uncertain.</p><h2>Elevation trend segments</h2><table><tr><th>Session</th><th>Time (s)</th><th>Pressure-height trend</th><th>Fitted change</th></tr>"
    for name, analysis in summary["elevation_segments"].items():
        for z in analysis["segments"]:
            html += f"<tr><td>{name}</td><td>{z['start_s']:.0f}–{z['end_s']:.0f}</td><td>{z['classification']}</td><td>{z['fitted_change_m']:+.2f} m</td></tr>"
    html += "</table><p>BIC-selected piecewise linear fit; 0.2 m noise floor; six 2-second bins minimum. Boundaries are approximate, not verified stair entries.</p></section>"

    html += (
        "<section><h2>Radio fingerprints held out from sensor matching</h2>"
        + radio_charts
        + "<p>Only populated 10-second bins are shown; missing-time gaps are compressed. Median RSSI per transmitter; weighted Jaccard includes absent transmitters. Bluetooth transmitters can move or randomize addresses, and Wi-Fi observations can be cached. Raw identifiers are not exported. A rising match from upper-right to lower-left is reciprocal-order evidence, not a floor plan.</p></section>"
    )
    html += '<section><h2>Files and related reports</h2><p><a href="joint_summary.json">All measurements and sensitivity runs</a> · <a href="reciprocal_alignment.csv">Candidate cross-session timing</a> · <a href="refined_report.html">Session 2 heading experiment</a> · <a href="session3/refined_report.html">Session 3 heading experiment</a></p><p>DTW fingerprint matching is motivated by <a href="https://yshu.org/paper/jsac15magicol.pdf">Magicol</a>; this implementation is a two-trace diagnostic, not the paper’s full mapped localization system.</p></section>'
    (out / "joint_report.html").write_text(html)
    print(json.dumps({k: v for k, v in summary.items() if k != "radio"}, indent=2))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("database", type=Path)
    p.add_argument("--out", type=Path, default=Path("output"))
    a = p.parse_args()
    run(a.database, a.out)
