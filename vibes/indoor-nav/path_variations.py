"""Step-first route experiments. GPS agreement is a diagnostic, not ground truth."""

import argparse, json, sqlite3
from pathlib import Path
import numpy as np
from gps_heading import (
    load_session,
    facing,
    smooth,
    anchors,
    circular_mean,
    gps_cluster,
)
from refine import wrap

ROOT = Path(__file__).resolve().parent


def sample(t, h, steps):
    ix = np.clip(np.searchsorted(t, steps, side="right") - 1, 0, len(t) - 1)
    values = h[ix].copy()
    values[(steps < t[0]) | (steps > t[-1])] = np.nan
    return values


def offset_fit(ss, h, aa):
    delta = []
    for a in aa:
        if not a["accepted"]:
            continue
        ts = ss["steps"][(ss["steps"] >= a["start_s"]) & (ss["steps"] <= a["end_s"])]
        vals = sample(ss["t"], h, ts)
        vals = vals[np.isfinite(vals)]
        if len(vals) < 5:
            continue
        mu, r = circular_mean(vals)
        if r > 0.5:
            delta.append(wrap(a["direction_rad"] - mu))
    mu, r = circular_mean(delta)
    return (float(mu) if delta and r > 0.5 else 0), len(delta)


def integrate(steps, head, length, start, updates=(), holdout=None):
    """No movement without a detected step. Missing directions explicitly freeze displacement."""
    p = np.array(start, dtype=float)
    rows = [[0.0, *p, 0]]
    corrections = []
    misses = 0
    correction = np.zeros(2)
    events = [(float(t), 0, i) for i, t in enumerate(steps)] + [
        (u["t"], 1, i) for i, u in enumerate(updates)
    ]
    for t, kind, i in sorted(events):
        if kind == 0:
            if np.isfinite(head[i]):
                p += length * np.array([np.cos(head[i]), np.sin(head[i])])
            else:
                misses += 1
            rows.append([t, *p, misses])
        else:
            u = updates[i]
            if (
                holdout
                and u["source_end"] >= holdout[0]
                and u["source_start"] <= holdout[1]
            ):
                continue
            innovation = np.array(u["xy"]) - p
            # Conservative positional correction: no forced GPS endpoint or route closure.
            delta = 0.25 * innovation
            norm = np.linalg.norm(delta)
            if norm > 5:
                delta *= 5 / norm
            p += delta
            correction += delta
            corrections.append(
                {
                    "t": t,
                    "shift_m": float(np.linalg.norm(delta)),
                    "innovation_m": float(np.linalg.norm(innovation)),
                }
            )
            rows.append([t, *p, misses])
    return np.array(rows), corrections, misses


def independent(aa):
    chosen = []
    for a in aa:
        if a["accepted"] and (not chosen or a["start_s"] > chosen[-1]["end_s"] + 5):
            chosen.append(a)
    return chosen


def position_at(rows, t):
    return rows[max(0, np.searchsorted(rows[:, 0], t, side="right") - 1), 1:3]


def main(db):
    out = ROOT / "output/path_variations"
    out.mkdir(exist_ok=True)
    c = sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    origin = c.execute(
        "select latitude,longitude from locations where session_id=2 and provider='gps' order by elapsed_realtime_ns limit 1"
    ).fetchone()
    sessions = {sid: load_session(c, sid, origin) for sid in [2, 3]}
    prepared = {}
    for sid, ss in sessions.items():
        a, _ = facing(ss["q"], [0, 0, -1])
        g, _ = facing(ss["qg"], [0, 0, -1])
        t = ss["t"]
        h = smooth(t, a, ss["q"], [0, 0, -1], 7, "median")
        game = smooth(t, g, ss["qg"], [0, 0, -1], 7, "mean")
        # Align gyro-based arbitrary frame to magnetic frame in first 10 s only.
        ix = (t <= t[0] + 10) & np.isfinite(h) & np.isfinite(game)
        frame, _ = circular_mean(h[ix] - game[ix])
        game = wrap(game + frame)
        robust = np.load(
            ROOT
            / "output"
            / ("session3" if sid == 3 else "")
            / "refinement_signals.npz"
        )
        ri = np.clip(
            np.searchsorted(robust["centers"], t), 0, len(robust["centers"]) - 1
        )
        axis = robust["robust_axis"][ri]
        good = (
            robust["robust_good"][ri]
            & (t >= robust["centers"][0])
            & (t <= robust["centers"][-1])
        )
        prepared[sid] = {
            "camera": h,
            "gyro": game,
            "axis": axis,
            "good": good,
            "anchors": anchors(ss),
            "strict": anchors(ss, radius_scale=1.5),
        }
    result = {
        "origin": list(origin),
        "sessions": {},
        "notes": [
            "No outdoor classifier is available. GPS quality gates are proxies, not proof of outdoor accuracy.",
            "Phone heading can change independently of walking direction. Step count supplies distance only after a step-length assumption.",
            "All routes use one initial recorded GPS fix for placement only. Relative step paths can instead be translated to a user-supplied start.",
            "Offsets are calibrated on the OTHER recording. This tests transfer between these two trips; it is not GPS-free calibration or live generalization.",
            "Centered heading smoothing uses future orientation samples. This experiment is an offline reconstruction.",
        ],
    }
    for sid, ss in sessions.items():
        src = prepared[sid]
        other = 3 if sid == 2 else 2
        other_ss = sessions[other]
        op = prepared[other]
        off, n = offset_fit(other_ss, op["camera"], op["anchors"])
        goff, gn = offset_fit(other_ss, op["gyro"], op["anchors"])
        cam = wrap(src["camera"] + off)
        gyro = wrap(src["gyro"] + goff)
        axis = src["axis"]
        axis_signed = cam + wrap(2 * (axis - cam)) / 2
        corrected = cam.copy()
        use = (
            src["good"]
            & np.isfinite(cam)
            & (abs(wrap(axis_signed - cam)) < np.radians(30))
        )
        corrected[use] = wrap(0.0 + cam[use] + 0.5 * wrap(axis_signed[use] - cam[use]))
        # Recalibration events use only past GPS windows and are at least 20 s apart.
        updates = {}
        for name, aa in [("hybrid", src["anchors"]), ("strict", src["strict"])]:
            uu = []
            for a in aa:
                if not a["accepted"]:
                    continue
                stamp = (
                    a["end_s"] + 2
                )  # cluster and longest sensitivity window can look ahead; mark actual availability below
                stamp = max(stamp, a["center_s"] + 17)
                if uu and stamp - uu[-1]["t"] < 20:
                    continue
                cluster = gps_cluster(ss["providers"]["gps"], a["end_s"])
                uu.append(
                    {
                        "t": stamp,
                        "xy": cluster[0].tolist(),
                        "source_start": a["center_s"] - 17,
                        "source_end": stamp,
                        "measurement_t": a["end_s"],
                    }
                )
            updates[name] = uu
        # Extrapolate each accepted location from measurement time to update time using steps.
        for uu in updates.values():
            for u in uu:
                u["raw_xy"] = u["xy"][:]
        methods = {
            "steps_camera": ("Steps + phone direction", cam, []),
            "steps_gyro": ("Steps + gyro direction", gyro, []),
            "steps_axis": ("Steps + supported gait axis", corrected, []),
            "hybrid": ("Steps + occasional GPS", cam, updates["hybrid"]),
            "strict": ("Steps + stricter GPS", cam, updates["strict"]),
        }
        rr = {
            "offset_training_session": other,
            "camera_offset_deg": float(np.degrees(off)),
            "gyro_offset_deg": float(np.degrees(goff)),
            "axis_corrected_samples": int(use.sum()),
            "detected_steps": len(ss["steps"]),
            "initial_fix_time_s": float(ss["providers"]["gps"][0, 0]),
            "variants": {},
            "validation_windows": independent(src["anchors"]),
            "quality_counts": {
                "nominal": len(updates["hybrid"]),
                "radius_1_5": len(updates["strict"]),
                "radius_2": sum(a["accepted"] for a in anchors(ss, radius_scale=2)),
            },
        }
        # Put a step-first relative origin at time 0. Initial placement uncertainty is explicit.
        start = ss["providers"]["gps"][0, 1:3]
        for key, (label, h, uu) in methods.items():
            hh = sample(ss["t"], h, ss["steps"])
            bylength = {}
            for length in [0.55, 0.7, 0.85]:
                us = []
                for u in uu:
                    u = dict(u)
                    mask = (
                        (ss["steps"] > u["measurement_t"])
                        & (ss["steps"] <= u["t"])
                        & np.isfinite(hh)
                    )
                    shift = length * np.array(
                        [np.cos(hh[mask]).sum(), np.sin(hh[mask]).sum()]
                    )
                    u["xy"] = (np.array(u["raw_xy"]) + shift).tolist()
                    us.append(u)
                rows, corr, miss = integrate(ss["steps"], hh, length, start, us)
                # Never count a GPS correction inside/near the test window as validation.
                checks = []
                for a in rr["validation_windows"]:
                    held = (a["start_s"] - 5, a["end_s"] + 5)
                    test, _, _ = integrate(ss["steps"], hh, length, start, us, held)
                    pred = position_at(test, a["end_s"]) - position_at(
                        test, a["start_s"]
                    )
                    target = np.array(a["end_xy_m"]) - a["start_xy_m"]
                    checks.append(
                        {
                            "start_s": a["start_s"],
                            "end_s": a["end_s"],
                            "vector_mismatch_m": float(np.linalg.norm(pred - target)),
                            "predicted_chord_m": float(np.linalg.norm(pred)),
                            "gps_chord_m": float(np.linalg.norm(target)),
                            "gps_radius_sum_m": a["radius_sum_m"],
                        }
                    )
                ll = []
                for row in rows:
                    lat = origin[0] + row[2] / 111320
                    lon = origin[1] + row[1] / (111320 * np.cos(np.radians(origin[0])))
                    ll.append(
                        [round(row[0], 3), round(lat, 8), round(lon, 8), int(row[3])]
                    )
                bylength[str(length)] = {
                    "path": ll,
                    "missed_heading_steps": miss,
                    "integrated_distance_m": round((len(hh) - miss) * length, 2),
                    "corrections": corr,
                    "heldout_intervals": checks,
                    "median_vector_mismatch_m": float(
                        np.median([x["vector_mismatch_m"] for x in checks])
                    )
                    if checks
                    else None,
                    "net_displacement_m": float(np.linalg.norm(rows[-1, 1:3] - start)),
                }
            rr["variants"][key] = {"label": label, "lengths": bylength}
        result["sessions"][str(sid)] = rr
    (out / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    (ROOT / "dashboard/path_data.js").write_text(
        "window.PATHDATA="
        + json.dumps(result, separators=(",", ":"), allow_nan=False)
        + ";\n"
    )
    lines = [
        "# Step-first route experiments",
        "",
        *["- " + x for x in result["notes"]],
        "",
        "## Results at 0.70 m per step",
        "",
        "Mismatch compares predicted displacement with separate, non-overlapping GPS intervals. It is **not true route error**. Hybrid GPS corrections whose source overlaps the held-out interval plus a 5-second margin are omitted. Because the metric is relative displacement, corrections outside the test interval cancel; this test evaluates step direction/distance, not the long-range benefit of positional resets. There are only 2 / 3 independent test intervals.",
        "",
        "| Session | Variation | Integrated distance | Missing-heading steps | GPS corrections | Median held-out vector mismatch |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for sid, r in result["sessions"].items():
        for key, v in r["variants"].items():
            z = v["lengths"]["0.7"]
            lines.append(
                f"| {sid} | {v['label']} | {z['integrated_distance_m']:.1f} m | {z['missed_heading_steps']} | {len(z['corrections'])} | {z['median_vector_mismatch_m']:.1f} m |"
            )
    lines += [
        "",
        "## Interpretation",
        "",
        "Occasional GPS changes global placement, but does not solve phone-to-body direction ambiguity. Each update moves the estimate 25% toward an informative past location, capped at 5 m; the location is advanced to update time using steps. Update times include a 17-second look-ahead allowance from the GPS consistency tests, so no future GPS sample is used at the correction event. No stair endpoint, route closure, building geometry or return-route symmetry is forced.",
        "",
        "The gait-axis method only nudges camera heading where prior robust acceleration tests pass and the signed axis agrees within 30°. Its 180° sign is still inherited from phone heading. The gyro method aligns its arbitrary frame using only the first 10 seconds of magnetic orientation, then transfers a direction offset from the other trip.",
        "",
        "Changing 0.55 / 0.70 / 0.85 m step length exposes distance uncertainty. Stair steps can have shorter horizontal travel, but automatically shortening them would require another unverified assumption; these trials keep length fixed. Missed-heading steps freeze displacement and are counted explicitly. The displayed line does not represent uncertainty bounds.",
        "",
        "**Recommended next experiment:** record a known start, a measured straight walk for personal step-length calibration, and a known phone/body orientation. Occasional genuinely reliable outdoor fixes could then constrain accumulated drift. Current data cannot identify outdoor sections confidently or establish which full route is correct.",
    ]
    lines += [
        "",
        "## Step-length sensitivity (phone direction)",
        "",
        "| Session | 0.55 m step | 0.70 m step | 0.85 m step |",
        "|---|---:|---:|---:|",
    ]
    for sid, r in result["sessions"].items():
        z = r["variants"]["steps_camera"]["lengths"]
        lines.append(
            "| "
            + sid
            + " | "
            + " | ".join(
                f"{z[k]['median_vector_mismatch_m']:.1f} m"
                for k in ["0.55", "0.7", "0.85"]
            )
            + " |"
        )
    lines += [
        "",
        "Values are median short-interval GPS vector mismatch. Session 2 prefers 0.70 m while session 3 prefers 0.85 m among these trials. That disagreement, sparse validation, and noisy GPS prevent selecting a universal step length. The gyro variant is worse on the return; supported gait-axis corrections make little difference. The most useful baseline here is steps plus smoothed phone direction, with occasional quality-gated GPS as a separately testable drift correction—not a proven full-route improvement.",
    ]
    (out / "FINDINGS.md").write_text("\n".join(lines) + "\n")
    print(
        "\n".join(
            lines[
                lines.index(
                    "| Session | Variation | Integrated distance | Missing-heading steps | GPS corrections | Median held-out vector mismatch |"
                ) :
            ][:12]
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("database", type=Path)
    main(p.parse_args().database)
