"""Step-synchronous quadrature heading candidates, with abstention and robustness checks.
The gait-phase sign is a model convention, not a validated forward direction.
"""

import csv, json
import numpy as np
from reconstruct import interp, axes, bandpass, chart, path, polyline


def wrap(a):
    return np.angle(np.exp(1j * a))


def cycle_vectors(t, world, steps):
    """Detrend each step and extract its fundamental cross-spectrum with vertical.

    Im(H * conj(V)) selects the horizontal component in quadrature with vertical.
    Per-cycle phase cancels: no heel-strike phase or phone azimuth is required.
    Arbitrary vertical-correlated hand motion is still an unresolvable confound.
    """
    times, vectors, strengths = [], [], []
    phase = np.arange(64) / 64
    design = np.column_stack([np.ones(64), phase - 0.5])
    for a, b in zip(steps[:-1], steps[1:]):
        if not 0.35 <= b - a <= 1.2 or a < t[0] or b > t[-1]:
            continue
        x = interp(a + (b - a) * phase, t, world)
        x -= design @ np.linalg.lstsq(design, x, rcond=None)[0]
        z = 2 * np.mean(x * np.exp(-2j * np.pi * phase)[:, None], axis=0)
        v = np.imag(z[:2] * np.conj(z[2])) / max(abs(z[2]), 1e-9)
        times.append((a + b) / 2)
        vectors.append(v)
        strengths.append(abs(z[2]))
    return np.array(times), np.array(vectors), np.array(strengths)


def summarize_vectors(times, vectors, centers, window=8):
    result = []
    rng = np.random.default_rng(1234)
    for c in centers:
        v = vectors[abs(times - c) <= window / 2]
        amp = np.linalg.norm(v, axis=1)
        v = v[amp > 0.1]
        amp = amp[amp > 0.1]
        if len(v) < 5:
            result.append([np.nan, 0, 180, 180, len(v), 0])
            continue
        # Unit-vector mean prevents a single large hand acceleration dominating.
        u = v / amp[:, None]
        m = u.mean(0)
        angle = np.arctan2(m[1], m[0])
        r = np.linalg.norm(m)
        draws = u[rng.integers(0, len(u), (300, len(u)))].mean(1)
        ci = np.percentile(
            abs(np.degrees(wrap(np.arctan2(draws[:, 1], draws[:, 0]) - angle))), 90
        )
        # Interleaved cycles challenge left/right gait and step-phase alternation.
        aa = [np.arctan2(*(u[j::2].mean(0)[::-1])) for j in [0, 1]]
        split = abs(np.degrees(wrap(aa[0] - aa[1])))
        result.append([angle, r, ci, split, len(v), np.median(amp)])
    return np.array(result)


def plateau_turns(centers, angles, good, variants):
    """Require repeatable before/after plateaus; allow noisy transition itself."""
    candidates = []
    for c in centers:
        before = (centers >= c - 8) & (centers <= c - 3)
        after = (centers >= c + 3) & (centers <= c + 8)
        if before.sum() < 5 or after.sum() < 5:
            continue
        if np.sum(good & before) < 4 or np.sum(good & after) < 4:
            continue
        phases = []
        spreads = []
        for mask in [before & good, after & good]:
            aa = angles[mask]
            m = np.angle(np.mean(np.exp(1j * aa)))
            phases.append(m)
            spreads.append(np.percentile(abs(np.degrees(wrap(aa - m))), 90))
        change = np.degrees(wrap(phases[1] - phases[0]))
        if abs(change) < 35 or max(spreads) > 20:
            continue
        deltas = []
        for a in variants:
            if np.sum(np.isfinite(a[before])) < 4 or np.sum(np.isfinite(a[after])) < 4:
                continue
            b = np.angle(np.nanmean(np.exp(1j * a[before])))
            e = np.angle(np.nanmean(np.exp(1j * a[after])))
            deltas.append(np.degrees(wrap(e - b)))
        if (
            len(deltas) < 3
            or max(abs(np.degrees(wrap(np.radians(deltas) - np.radians(change))))) > 20
        ):
            continue
        candidates.append(
            {
                "time_s": float(c),
                "change_deg_model_sign": float(change),
                "plateau_spread_deg": float(max(spreads)),
                "variant_changes_deg": deltas,
                "support_score": float(np.sum(good & (before | after))),
            }
        )
    # Nonmaximum suppression groups adjacent change times; no precision finer than windows.
    selected = []
    for z in sorted(
        candidates, key=lambda z: (-z["support_score"], z["plateau_spread_deg"])
    ):
        if all(abs(z["time_s"] - x["time_s"]) > 12 for x in selected):
            selected.append(z)
    return sorted(selected, key=lambda z: z["time_s"])


def robust_cycle_axes(t, world, steps, centers, window):
    filtered = bandpass(world)
    times, vecs = [], []
    for a, b in zip(steps[:-1], steps[1:]):
        if not 0.35 <= b - a <= 1.2:
            continue
        x = filtered[(t >= a) & (t < b), :2]
        if len(x) < 10:
            continue
        ev, v = np.linalg.eigh(np.cov(x.T))
        if ev[-1] / max(ev.sum(), 1e-9) < 0.6:
            continue
        theta = np.arctan2(v[1, -1], v[0, -1])
        times.append((a + b) / 2)
        vecs.append([np.cos(2 * theta), np.sin(2 * theta)])
    stats = summarize_vectors(np.array(times), np.array(vecs), centers, window)
    stats[:, [0, 2, 3]] /= 2
    return stats


def analyze(
    t, world, game, linear_world, step_t, centers, baseline, baseline_good, gyro, out
):
    ct, cv, vamp = cycle_vectors(t, world, step_t)
    gt, gv, _ = cycle_vectors(t, game, step_t)
    lt, lv, _ = cycle_vectors(t, linear_world, step_t)
    estimates = {w: summarize_vectors(ct, cv, centers, w) for w in [6, 8, 10]}
    main = estimates[8]
    g = summarize_vectors(gt, gv, centers, 8)
    lin = summarize_vectors(lt, lv, centers, 8)
    angle = main[:, 0]
    finite = np.isfinite(angle) & np.isfinite(g[:, 0])
    align = np.angle(np.mean(np.exp(1j * (angle[finite] - g[finite, 0]))))
    gangle = wrap(g[:, 0] + align)
    mismatch = abs(np.degrees(wrap(angle - gangle)))
    wspread = np.maximum(
        abs(np.degrees(wrap(angle - estimates[6][:, 0]))),
        abs(np.degrees(wrap(angle - estimates[10][:, 0]))),
    )
    ldiff = abs(np.degrees(wrap(angle - lin[:, 0])))
    good = (
        (main[:, 1] >= 0.75)
        & (main[:, 2] <= 20)
        & (main[:, 3] <= 20)
        & (main[:, 4] >= 7)
        & (main[:, 5] >= 0.15)
        & (mismatch <= 20)
        & (wspread <= 20)
        & (ldiff <= 20)
    )
    turns = plateau_turns(
        centers,
        angle,
        good,
        [estimates[6][:, 0], estimates[10][:, 0], gangle, lin[:, 0]],
    )
    robust = {w: robust_cycle_axes(t, world, step_t, centers, w) for w in [6, 8, 10]}
    ra = robust[8]
    rg = robust_cycle_axes(t, game, step_t, centers, 8)
    rl = robust_cycle_axes(t, linear_world, step_t, centers, 8)
    finite_r = np.isfinite(ra[:, 0]) & np.isfinite(rg[:, 0])
    r_offset = 0.5 * np.angle(np.mean(np.exp(2j * (ra[finite_r, 0] - rg[finite_r, 0]))))
    axial_diff = lambda a, b: abs(np.degrees(wrap(2 * (a - b)) / 2))
    rws = np.maximum(
        axial_diff(ra[:, 0], robust[6][:, 0]), axial_diff(ra[:, 0], robust[10][:, 0])
    )
    rmis = axial_diff(ra[:, 0], rg[:, 0] + r_offset)
    rld = axial_diff(ra[:, 0], rl[:, 0])
    rgood = (
        (ra[:, 1] >= 0.75)
        & (ra[:, 2] <= 20)
        & (ra[:, 3] <= 20)
        & (ra[:, 4] >= 7)
        & (rws <= 20)
        & (rmis <= 20)
        & (rld <= 20)
    )
    # Reuse plateau detector in doubled-angle space: thresholds are twice physical angles.
    rturns = plateau_turns(
        centers,
        2 * ra[:, 0],
        rgood,
        [
            2 * robust[6][:, 0],
            2 * robust[10][:, 0],
            2 * (rg[:, 0] + r_offset),
            2 * rl[:, 0],
        ],
    )
    for tr in rturns:
        tr["axis_change_deg_mod180"] = tr.pop("change_deg_model_sign") / 2
        tr["plateau_spread_deg"] /= 2
        tr["variant_changes_deg"] = [v / 2 for v in tr["variant_changes_deg"]]
    rturns = [tr for tr in rturns if abs(tr["axis_change_deg_mod180"]) >= 35]

    # Compare SAME metric and window choices, avoiding a pass-rate-only claim of improvement.
    b6, _ = axes(t, bandpass(world), centers, 6)
    b8, _ = axes(t, bandpass(world), centers, 8)
    b10, _ = axes(t, bandpass(world), centers, 10)
    bspread = np.maximum(
        abs(np.degrees(wrap(2 * (b8 - b6)) / 2)),
        abs(np.degrees(wrap(2 * (b8 - b10)) / 2)),
    )
    # Movement episodes are sustained step support, not a route segmentation.
    episodes = []
    begin = step_t[0]
    for a, b in zip(step_t[:-1], step_t[1:]):
        if b - a > 2:
            episodes.append([float(begin), float(a)])
            begin = b
    episodes.append([float(begin), float(step_t[-1])])
    supported = []
    for i in np.flatnonzero(good):
        if not supported or centers[i] - supported[-1][-1] > 1.01:
            supported.append([float(centers[i]), float(centers[i])])
        else:
            supported[-1][-1] = float(centers[i])
    metrics = {
        "method": "step-synchronous horizontal/vertical quadrature; model-dependent sign",
        "valid_step_cycles": len(ct),
        "evaluated_windows": len(centers),
        "supported_windows": int(good.sum()),
        "supported_window_fraction": float(good.mean()),
        "supported_center_intervals_s": supported,
        "repeatable_turn_candidates": turns,
        "detector_supported_movement_episodes_s": episodes,
        "baseline_axis_window_sensitivity_deg_p50_p90": np.percentile(
            bspread, [50, 90]
        ).tolist(),
        "quadrature_window_sensitivity_deg_p50_p90": np.nanpercentile(
            wspread, [50, 90]
        ).tolist(),
        "quadrature_rv_game_disagreement_deg_p50_p90": np.nanpercentile(
            mismatch, [50, 90]
        ).tolist(),
        "quadrature_linear_sensor_disagreement_deg_p50_p90": np.nanpercentile(
            ldiff, [50, 90]
        ).tolist(),
        "median_cycle_horizontal_quadrature_amplitude_mps2": float(
            np.median(np.linalg.norm(cv, axis=1))
        ),
        "phase_model_caveat": "Signed directions assume stable forward/vertical gait phase. Correlated hand swing can invalidate this, including apparent reversals. Bootstrap measures within-model repeatability only.",
    }
    # Turn scan without new gates is diagnostics, never promoted to accepted events.
    diagnostics = []
    for c in np.arange(15, 180, 5):
        b = (centers >= c - 8) & (centers <= c - 3)
        e = (centers >= c + 3) & (centers <= c + 8)
        if (
            b.sum()
            and e.sum()
            and np.isfinite(angle[b]).any()
            and np.isfinite(angle[e]).any()
        ):
            delta = np.degrees(
                wrap(
                    np.angle(np.nanmean(np.exp(1j * angle[e])))
                    - np.angle(np.nanmean(np.exp(1j * angle[b])))
                )
            )
            diagnostics.append(
                {
                    "time_s": float(c),
                    "change_deg": float(delta),
                    "before_supported": int(good[b].sum()),
                    "after_supported": int(good[e].sum()),
                }
            )
    metrics["turn_scan_diagnostics"] = diagnostics
    metrics["robust_cycle_pca"] = {
        "supported_windows": int(rgood.sum()),
        "supported_fraction": float(rgood.mean()),
        "window_sensitivity_deg_p50_p90": np.nanpercentile(rws, [50, 90]).tolist(),
        "repeatable_axis_changes": rturns,
        "supported_center_times_s": centers[rgood].tolist(),
        "note": "Per-cycle PCA, equal-weight axial consensus, split-cycle/bootstrap/multi-stream checks. Axis modulo 180; not a signed turn or heading.",
    }
    with (out / "robust_axis_windows.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "center_s",
                "axis_deg_mod180",
                "resultant",
                "bootstrap_halfwidth_deg",
                "split_difference_deg",
                "cycle_count",
                "window_sensitivity_deg",
                "frame_difference_deg",
                "linear_sensor_difference_deg",
                "supported",
            ]
        )
        w.writerows(
            (z, np.degrees(a[0]), *a[1:5], ws, mm, ld, bool(ok))
            for z, a, ws, mm, ld, ok in zip(centers, ra, rws, rmis, rld, rgood)
        )

    (out / "refined_summary.json").write_text(
        json.dumps(metrics, indent=2, allow_nan=False)
    )
    with (out / "heading_windows.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "center_s",
                "quadrature_angle_deg_model_sign",
                "resultant",
                "bootstrap_90pct_halfwidth_deg",
                "odd_even_difference_deg",
                "cycle_count",
                "amplitude_mps2",
                "rv_game_diff_deg",
                "window_sensitivity_deg",
                "linear_sensor_diff_deg",
                "supported",
            ]
        )
        w.writerows(
            (
                z,
                float(np.degrees(a[0])),
                *a[1:],
                float(mm),
                float(ws),
                float(ld),
                bool(ok),
            )
            for z, a, mm, ws, ld, ok in zip(
                centers, main, mismatch, wspread, ldiff, good
            )
        )
    np.savez(
        out / "refinement_signals.npz",
        t=t,
        world=world,
        game=game,
        linear_world=linear_world,
        step_t=step_t,
        centers=centers,
        angle=angle,
        good=good,
        baseline=baseline,
        robust_axis=ra[:, 0],
        robust_good=rgood,
    )
    html = '<!doctype html><meta charset="utf-8"><title>Step-synchronous reconstruction</title><style>body{font:16px system-ui;max-width:1040px;margin:30px auto;padding:20px;background:#eef2f6;color:#172533}section{padding:24px;background:white;margin:20px 0;border-radius:12px}svg{width:100%;font:12px system-ui}.title{font-size:17px;font-weight:650}.legend{font-size:13px}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}table{width:100%;border-collapse:collapse}</style>'
    html += f"<h1>Step-synchronous direction: stronger checks</h1><p>{int(good.sum())} / {len(centers)} windows pass the new repeatability checks; {len(turns)} repeatable turn candidates. This is not an accuracy score.</p><section><h2>What changed</h2><p>Estimate the horizontal acceleration component synchronized in quadrature with vertical walking motion, step by step. Give each cycle equal weight, then require agreement across odd/even steps, window lengths, orientation streams and gravity subtraction methods. Phone azimuth is never used as walking heading.</p><p>Forward sign still depends on an unvalidated gait-phase model. Free-hand movement can be synchronized with steps. Sensor-only consistency cannot prove the person followed the inferred heading.</p></section>"
    display = np.degrees(angle)
    html += (
        "<section>"
        + chart(
            [("model-signed heading", centers, display, "#2166ac")],
            "Step-synchronous heading (wrapped)",
            "degrees; time (s)",
        )
        + chart(
            [
                ("supported", centers, good.astype(float), "#228b69"),
                ("cycle directional concentration", centers, main[:, 1], "#e08214"),
            ],
            "Repeatability and support",
            "0–1; time (s)",
        )
        + "</section>"
    )
    html += (
        "<section>"
        + chart(
            [
                ("PCA axis", centers, bspread, "#a65a00"),
                (
                    "step quadrature",
                    centers,
                    np.nan_to_num(wspread, nan=180),
                    "#2166ac",
                ),
            ],
            "Sensitivity to 6 / 8 / 10-second windows",
            "degrees; time (s)",
        )
        + "<p>Both methods compared on the same time grid and window lengths. PCA differences are modulo 180°; quadrature differences are modulo 360°, so the latter also penalizes sign flips.</p></section>"
    )
    html += (
        "<section><h2>Candidate turns</h2><p>Require ≥4 supported centers on each of two plateaus 3–8 seconds before/after the event, plateau spread ≤20°, change ≥35°, and change agreement within 20° across four variants. Transitions may be noisy. Timing is approximate (several seconds).</p><pre>"
        + json.dumps(turns, indent=2)
        + "</pre><h2>Supported center intervals (seconds)</h2><p>"
        + str(supported)
        + "</p><p>Overlapping windows are not independent samples. Coverage is conditional, and gaps must remain unknown.</p></section>"
    )
    html += '<section><h2>Outputs and method references</h2><p><a href="refined_summary.json">Measured comparison</a> · <a href="heading_windows.csv">Every heading window</a> · <a href="report.html">Baseline and elevation</a></p><p>Phase-based heading has precedent in <a href="https://onlinelibrary.wiley.com/doi/10.1155/2018/5607036">Deng et al. (2018)</a>. This is an independently implemented diagnostic method, not a validated reproduction of that paper.</p></section>'
    html += (
        "<section><h2>Robust per-cycle PCA alternative</h2><p>"
        + str(int(rgood.sum()))
        + " supported windows; "
        + str(len(rturns))
        + " repeatable axis changes. Equal-weight cycle axes reduce domination by individual hand impulses. These remain unsigned motion axes.</p>"
        + chart(
            [
                (
                    "robust PCA axis window sensitivity",
                    centers,
                    np.nan_to_num(rws, nan=90),
                    "#228b69",
                ),
                ("baseline PCA", centers, bspread, "#a65a00"),
            ],
            "Same-window axis sensitivity comparison",
            "degrees; time (s)",
        )
        + "<pre>"
        + json.dumps(rturns, indent=2)
        + '</pre><p><a href="robust_axis_windows.csv">Every robust axis window</a></p></section>'
    )
    (out / "refined_report.html").write_text(html)
    return metrics
