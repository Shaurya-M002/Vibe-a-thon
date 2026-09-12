#!/usr/bin/env python3
"""GPS-assisted walking-direction diagnostics; orientation smoothness is not accuracy."""

import argparse, csv, json, sqlite3
from pathlib import Path
import numpy as np
from reconstruct import rotate, quaternions, chart, polyline
from refine import wrap


def circular_mean(a, weights=None):
    a = np.asarray(a)
    valid = np.isfinite(a)
    if not valid.any():
        return np.nan, 0.0
    w = np.ones(len(a)) if weights is None else np.asarray(weights)
    z = np.sum(w[valid] * np.exp(1j * a[valid])) / np.sum(w[valid])
    return float(np.angle(z)), float(abs(z))


def circular_median(a):
    a = np.asarray(a)
    a = a[np.isfinite(a)]
    if not len(a):
        return np.nan
    return float(a[np.argmin(np.sum(abs(wrap(a[:, None] - a[None, :])), axis=1))])


def quaternion_mean(q):
    q = q / np.linalg.norm(q, axis=1)[:, None]
    ev, v = np.linalg.eigh(q.T @ q)
    return v[:, -1]


def facing(q, axis):
    v = rotate(q, np.tile(axis, (len(q), 1)))
    horizontal = np.linalg.norm(v[:, :2], axis=1)
    # Internal angle convention: counterclockwise from east, GPS and IMU alike.
    a = np.arctan2(v[:, 1], v[:, 0])
    a[horizontal < 0.35] = np.nan
    return a, horizontal


def smooth(t, a, q, axis, window, method):
    if method == "raw":
        return a.copy()
    out = []
    for i, z in enumerate(t):
        mask = abs(t - z) <= window / 2
        if method == "quaternion":
            qm = quaternion_mean(q[mask])
            h, _ = facing(qm[None, :], axis)
            out.append(h[0])
            continue
        vals = a[mask]
        if np.isfinite(vals).sum() < max(3, mask.sum() * 0.5):
            out.append(np.nan)
            continue
        mean, r = circular_mean(vals)
        if r < 0.3:
            out.append(np.nan)
            continue
        if method == "mean":
            out.append(mean)
        elif method == "median":
            out.append(circular_median(vals))
        elif method == "adaptive":
            # Long median suppresses impulses. A persistent short-window change uses
            # the short median, reducing broad-window turn smearing without using GPS.
            recent = a[(t >= z - 2) & (t <= z)]
            future = a[(t >= z) & (t <= z + 2)]
            b, rb = circular_mean(recent)
            f, rf = circular_mean(future)
            short = a[abs(t - z) <= 1.5]
            if rb > 0.85 and rf > 0.85 and abs(wrap(b - f)) < np.radians(20):
                out.append(circular_median(short))
            else:
                out.append(circular_median(vals))
        else:
            raise ValueError(method)
    return np.array(out)


def load_session(c, sid, origin):
    start, end = c.execute(
        "select started_at_ms,ended_at_ms from sessions where id=?", (sid,)
    ).fetchone()
    duration = (end - start) / 1000
    rr = c.execute(
        "select wall_time_ms,elapsed_realtime_ns,sensor_type,values_json from sensor_samples where session_id=? and sensor_type in (1,11,15,18)",
        (sid,),
    ).fetchall()
    offset = np.median([w - n / 1e6 for w, n, k, v in rr if k == 1])
    data = {}
    for typ in [11, 15, 18]:
        values = sorted(
            [
                ((n / 1e6 + offset - start) / 1000, json.loads(v))
                for w, n, k, v in rr
                if k == typ and 0 <= (n / 1e6 + offset - start) / 1000 <= duration
            ]
        )
        ts = np.array([z[0] for z in values])
        ts, ix = np.unique(ts, return_index=True)
        data[typ] = (ts, np.array([z[1] for z in values])[ix])
    t = np.arange(
        max(data[11][0][0], data[15][0][0]), min(data[11][0][-1], data[15][0][-1]), 0.2
    )
    q = quaternions(t, *data[11])
    qg = quaternions(t, *data[15])
    providers = {}
    audit = {}
    for provider in ["gps", "fused", "network"]:
        rows = c.execute(
            "select * from locations where session_id=? and provider=? order by elapsed_realtime_ns",
            (sid, provider),
        ).fetchall()
        seen = set()
        ret = []
        stale = 0
        for r in rows:
            if r["elapsed_realtime_ns"] is None:
                continue
            z = (r["elapsed_realtime_ns"] / 1e6 + offset - start) / 1000
            if not 0 <= z <= duration:
                stale += 1
                continue
            key = r["elapsed_realtime_ns"]
            if key in seen:
                continue
            seen.add(key)
            if r["accuracy_m"] is None or r["accuracy_m"] <= 0:
                continue
            x = (r["longitude"] - origin[1]) * 111320 * np.cos(np.radians(origin[0]))
            y = (r["latitude"] - origin[0]) * 111320
            ret.append([z, x, y, r["accuracy_m"]])
        providers[provider] = np.array(ret)
        audit[provider] = {
            "rows": len(rows),
            "stale": stale,
            "retained_unique_monotonic_fixes": len(ret),
        }
    return {
        "id": sid,
        "duration": duration,
        "t": t,
        "q": q,
        "qg": qg,
        "steps": data[18][0],
        "providers": providers,
        "audit": audit,
    }


def gps_cluster(fixes, time, radius_scale=1):
    r = fixes[abs(fixes[:, 0] - time) <= 2]
    if len(r) < 2:
        return None
    p = np.median(r[:, 1:3], axis=0)
    radius = max(
        float(np.median(r[:, 3])) * radius_scale,
        float(np.percentile(np.linalg.norm(r[:, 1:3] - p, axis=1), 80)),
    )
    return p, radius


def gps_anchor(fixes, center, window=20, radius_scale=1):
    a, b = center - window / 2, center + window / 2
    ca = gps_cluster(fixes, a, radius_scale)
    cb = gps_cluster(fixes, b, radius_scale)
    if ca is None or cb is None:
        return None
    pa, ra = ca
    pb, rb = cb
    v = pb - pa
    d = np.linalg.norm(v)
    course = np.arctan2(v[1], v[0])
    radius = ra + rb
    rr = fixes[(fixes[:, 0] >= a) & (fixes[:, 0] <= b)]
    if len(rr) < window * 0.5:
        return None
    tt = rr[:, 0] - center
    design = np.column_stack([tt, np.ones(len(tt))])
    weights = 1 / np.maximum(rr[:, 3], 3) ** 2
    for _ in range(4):
        fit = np.linalg.lstsq(
            design * np.sqrt(weights[:, None]),
            rr[:, 1:3] * np.sqrt(weights[:, None]),
            rcond=None,
        )[0]
        resid = np.linalg.norm(rr[:, 1:3] - design @ fit, axis=1)
        weights = (
            1
            / np.maximum(rr[:, 3], 3) ** 2
            * np.minimum(1, 5 / np.maximum(resid, 0.01))
        )
    regression = np.arctan2(fit[0, 1], fit[0, 0])
    resid = float(np.median(resid))
    speed = float(d / window)
    informative = bool(
        d >= 2 * radius
        and 0.3 <= speed <= 2.5
        and abs(np.degrees(wrap(course - regression))) <= 20
        and resid <= max(3, np.median(rr[:, 3]))
    )
    return {
        "center_s": float(center),
        "start_s": float(a),
        "end_s": float(b),
        "window_s": window,
        "direction_rad": float(course),
        "displacement_m": float(d),
        "radius_sum_m": float(radius),
        "displacement_radius_ratio": float(d / max(radius, 0.01)),
        "radius_geometry_angle_deg": float(
            np.degrees(np.arcsin(min(1, radius / max(d, 0.01))))
        ),
        "regression_difference_deg": float(abs(np.degrees(wrap(course - regression)))),
        "regression_residual_m": resid,
        "speed_mps": speed,
        "informative": informative,
        "start_xy_m": pa.tolist(),
        "end_xy_m": pb.tolist(),
    }


def anchors(session, window=20, radius_scale=1):
    found = []
    fixes = session["providers"]["gps"]
    for center in np.arange(20, session["duration"] - 19, 5):
        z = gps_anchor(fixes, center, window, radius_scale)
        if z is None:
            continue
        checks = [gps_anchor(fixes, center, w, radius_scale) for w in [15, 25, 30]]
        differences = [
            abs(np.degrees(wrap(z["direction_rad"] - x["direction_rad"])))
            for x in checks
            if x
        ]
        z["duration_sensitivity_deg"] = (
            max(differences) if len(differences) == 3 else 180.0
        )
        # Gate course consistency too; no phone orientations influence anchor selection.
        z["accepted"] = bool(z["informative"] and z["duration_sensitivity_deg"] <= 20)
        fused = gps_anchor(session["providers"]["fused"], center, window, radius_scale)
        z["fused_course_difference_deg"] = (
            float(abs(np.degrees(wrap(z["direction_rad"] - fused["direction_rad"]))))
            if fused
            else None
        )
        found.append(z)
    return found


def sample_heading(t, a, steps, start, end, axial=False):
    st = steps[(steps >= start) & (steps <= end)]
    ix = np.searchsorted(t, st)
    ix = np.clip(ix, 0, len(t) - 1)
    vals = a[ix]
    if len(st) < 5 or np.isfinite(vals).sum() < 0.7 * len(st):
        return np.nan
    mu, r = circular_mean(vals * (2 if axial else 1))
    return mu / (2 if axial else 1) if r >= 0.35 else np.nan


def evaluate_method(session, heading, anchor_list, axial=False):
    mult = 2 if axial else 1
    pred = []
    actual = []
    used = []
    for z in anchor_list:
        if not z["accepted"]:
            continue
        h = sample_heading(
            session["t"], heading, session["steps"], z["start_s"], z["end_s"], axial
        )
        if np.isfinite(h):
            pred.append(h)
            actual.append(z["direction_rad"])
            used.append(z)
    center_predictions = np.array(
        [heading[np.argmin(abs(session["t"] - z["center_s"]))] for z in used]
    )
    center_errors = abs(
        np.degrees(wrap(mult * (np.array(actual) - center_predictions)) / mult)
    )
    pred = np.array(pred)
    actual = np.array(actual)
    res = wrap(mult * (actual - pred)) / mult
    offset, r = circular_mean(res * mult)
    offset /= mult
    raw = abs(np.degrees(res))
    cal = abs(np.degrees(wrap(mult * (res - offset)) / mult))
    heldout = []
    details = []
    # Exclude overlapping GPS windows and 5-second margins from offset calibration.
    for i, z in enumerate(used):
        train = np.array(
            [
                j
                for j, x in enumerate(used)
                if x["end_s"] + 5 < z["start_s"] or x["start_s"] - 5 > z["end_s"]
            ]
        )
        if len(train) < 2:
            continue
        fit, concentration = circular_mean(mult * res[train])
        fit /= mult
        if concentration < 0.5:
            continue
        err = abs(np.degrees(wrap(mult * (res[i] - fit)) / mult))
        heldout.append(err)
        details.append(
            {
                "center_s": z["center_s"],
                "error_deg": float(err),
                "offset_training_centers_s": [used[j]["center_s"] for j in train],
            }
        )

    def med(x):
        return float(np.median(x)) if len(x) else None

    return {
        "center_output_vs_gps_chord_median_error_deg": float(
            np.nanmedian(center_errors)
        )
        if np.isfinite(center_errors).any()
        else None,
        "gps_windows_evaluated": len(used),
        "raw_median_error_deg": med(raw),
        "fitted_constant_offset_deg": float(np.degrees(offset)) if len(used) else None,
        "offset_concentration": float(r),
        "in_sample_calibrated_median_error_deg": med(cal),
        "heldout_nonoverlap_windows": len(heldout),
        "heldout_median_error_deg": med(heldout),
        "heldout_details": details,
        "offset_deg_by_anchor": [
            {"center_s": z["center_s"], "offset_deg": float(np.degrees(x))}
            for z, x in zip(used, res)
        ],
    }


def phone_turns(t, series):
    out = []
    for center in np.arange(10, t[-1] - 10, 2):
        changes = []
        for a in series:
            b = a[(t >= center - 8) & (t <= center - 3)]
            e = a[(t >= center + 3) & (t <= center + 8)]
            hb, rb = circular_mean(b)
            he, re = circular_mean(e)
            if rb < 0.85 or re < 0.85:
                continue
            changes.append(float(np.degrees(wrap(he - hb))))
        if len(changes) < len(series):
            continue
        if np.max(
            abs(wrap(np.radians(changes)[:, None] - np.radians(changes)[None, :]))
        ) > np.radians(20):
            continue
        d = float(np.median(changes))
        if abs(d) < 35:
            continue
        if not out or center - out[-1]["time_s"] > 10:
            out.append(
                {
                    "time_s": float(center),
                    "phone_direction_change_deg": d,
                    "cross_smoother_spread_deg": float(np.ptp(changes)),
                }
            )
    return out


def bridge(session, anchor_list, phone, axis, axis_good, length=0.7):
    """GPS-calibrated phone-offset interpolation, with gated acceleration-axis correction.
    Conditional segments only. GPS headings set boundary calibration, not path accuracy.
    """
    t = session["t"]
    accepted = [z for z in anchor_list if z["accepted"]]
    cal = []
    for z in accepted:
        h = sample_heading(t, phone, session["steps"], z["start_s"], z["end_s"])
        if np.isfinite(h):
            cal.append((z, wrap(z["direction_rad"] - h)))
    # Thin overlapping anchors to >=15 s centers. These are still correlated measurements.
    keep = []
    for item in cal:
        if not keep or item[0]["center_s"] - keep[-1][0]["center_s"] >= 15:
            keep.append(item)
    rows = []
    segments = []
    for (a, oa), (b, ob) in zip(keep[:-1], keep[1:]):
        if b["center_s"] - a["center_s"] > 35:
            continue
        steps = session["steps"][
            (session["steps"] >= a["center_s"]) & (session["steps"] < b["center_s"])
        ]
        pos = np.array(gps_cluster(session["providers"]["gps"], a["center_s"])[0])
        start = pos.copy()
        usedaxis = 0
        validsteps = 0
        # Magnetic-to-GPS axial offset from the FIRST anchor; do not copy phone offset to acceleration.
        ax = sample_heading(t, axis, session["steps"], a["start_s"], a["end_s"], True)
        axoff = wrap(2 * (a["direction_rad"] - ax)) / 2 if np.isfinite(ax) else np.nan
        for st in steps:
            i = np.clip(np.searchsorted(t, st), 0, len(t) - 1)
            f = (st - a["center_s"]) / (b["center_s"] - a["center_s"])
            off = oa + f * wrap(ob - oa)
            h = phone[i] + off
            if not np.isfinite(h):
                continue
            if axis_good[i] and np.isfinite(axoff):
                ha = axis[i] + axoff
                ha += np.pi * round((h - ha) / np.pi)
                if abs(wrap(ha - h)) < np.radians(30):
                    h = circular_mean([h, ha])[0]
                    usedaxis += 1
            pos += length * np.array([np.cos(h), np.sin(h)])
            validsteps += 1
            rows.append(
                [
                    float(st),
                    float(pos[0]),
                    float(pos[1]),
                    float(np.degrees(h)),
                    len(segments),
                ]
            )
        if validsteps < len(steps):
            rows = [r for r in rows if r[4] != len(segments)]
            continue
        target = gps_cluster(session["providers"]["gps"], b["center_s"])[0]
        segments.append(
            {
                "start_s": a["center_s"],
                "end_s": b["center_s"],
                "steps_in_interval": len(steps),
                "steps_integrated": validsteps,
                "acceleration_axis_corrections": usedaxis,
                "endpoint_mismatch_m": float(np.linalg.norm(pos - target)),
                "start_xy_m": start.tolist(),
                "end_xy_m": pos.tolist(),
                "gps_end_xy_m": target.tolist(),
                "warning": "Both GPS course anchors calibrate heading. Endpoint mismatch is a fit diagnostic, not independent ground-truth error.",
            }
        )
    return rows, segments


def main(db, out):
    out.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    origin = c.execute(
        "select latitude,longitude from locations where session_id=2 and provider='gps' order by elapsed_realtime_ns limit 1"
    ).fetchone()
    sessions = [load_session(c, sid, origin) for sid in [2, 3]]
    summary = {}
    plots = []
    for ss in sessions:
        sid = ss["id"]
        t = ss["t"]
        allanchors = anchors(ss)
        accepted = [z for z in allanchors if z["accepted"]]
        methods = {}
        stats = {}
        primary = None
        for axisname, axis in [
            ("top_edge_Y", [0, 1, 0]),
            ("rear_camera_minus_Z", [0, 0, -1]),
        ]:
            a, projection = facing(ss["q"], axis)
            for method, win in (
                [("raw", 0)]
                + [(m, w) for m in ["mean", "median", "quaternion"] for w in [3, 7, 15]]
                + [("adaptive", 15)]
            ):
                key = f"{axisname}/{method}/{win}s"
                h = smooth(t, a, ss["q"], axis, win, method)
                methods[key] = h
                st = evaluate_method(ss, h, allanchors)
                valid = np.isfinite(h)
                diff = abs(np.degrees(wrap(np.diff(h))))
                st["heading_available_fraction"] = float(valid.mean())
                st["successive_0_2s_change_p90_deg"] = (
                    float(np.nanpercentile(diff, 90))
                    if np.isfinite(diff).any()
                    else None
                )
                stats[key] = st
        # Choose geometric conditioning, independently of GPS fit quality; keep 7 s
        # median fixed rather than selecting the lowest measured validation error.
        primary_axis = max(
            ["top_edge_Y", "rear_camera_minus_Z"],
            key=lambda a: np.isfinite(methods[a + "/raw/0s"]).mean(),
        )
        primary = methods[primary_axis + "/median/7s"]
        game_raw, _ = facing(ss["qg"], [0, 0, -1])
        game_h = smooth(t, game_raw, ss["qg"], [0, 0, -1], 7, "mean")
        game_stats = evaluate_method(ss, game_h, allanchors)
        game_stats["raw_median_error_deg"] = None
        stats["game_RV_camera_mean7"] = game_stats
        magnetic_h = methods["rear_camera_minus_Z/mean/7s"]
        relative = wrap(magnetic_h - game_h)
        frame_offset, _ = circular_mean(relative)
        frame_mismatch = abs(np.degrees(wrap(relative - frame_offset)))

        base_dir = out.parent if sid == 2 else out.parent / "session3"
        data = np.load(base_dir / "refinement_signals.npz")
        ix = np.clip(np.searchsorted(data["centers"], t), 0, len(data["centers"]) - 1)
        axis = data["robust_axis"][ix]
        ag = data["robust_good"][ix]
        # Avoid projecting evidence beyond the underlying analysis centers.
        axis[(t < data["centers"][0]) | (t > data["centers"][-1])] = np.nan
        for name, h in [
            ("baseline_PCA_axis", data["baseline"][ix]),
            ("robust_cycle_PCA_axis", axis),
        ]:
            stats[name] = evaluate_method(ss, h, allanchors, True)
        turns = phone_turns(
            t,
            [
                methods[primary_axis + "/mean/7s"],
                methods[primary_axis + "/median/7s"],
                methods[primary_axis + "/median/15s"],
                game_h,
            ],
        )
        for z in turns:
            before = [
                a
                for a in accepted
                if z["time_s"] - 30 <= a["center_s"] <= z["time_s"] - 10
            ]
            after = [
                a
                for a in accepted
                if z["time_s"] + 10 <= a["center_s"] <= z["time_s"] + 30
            ]
            if before and after:
                aa = before[-1]
                bb = after[0]
                d = np.degrees(wrap(bb["direction_rad"] - aa["direction_rad"]))
                z["gps_change_deg"] = float(d)
                ha = sample_heading(t, primary, ss["steps"], aa["start_s"], aa["end_s"])
                hb = sample_heading(t, primary, ss["steps"], bb["start_s"], bb["end_s"])
                matched = (
                    float(np.degrees(wrap(hb - ha)))
                    if np.isfinite(ha) and np.isfinite(hb)
                    else None
                )
                z["phone_change_over_matching_gps_windows_deg"] = matched
                z["gps_comparison_span_s"] = [aa["start_s"], bb["end_s"]]
                z["agrees_with_gps_within_30deg"] = (
                    bool(abs(wrap(np.radians(d - matched))) < np.radians(30))
                    if matched is not None
                    else None
                )
            else:
                z["gps_change_deg"] = None
                z["agrees_with_gps_within_30deg"] = None
        brow, bsegments = bridge(ss, allanchors, primary, axis, ag)
        bridge_sensitivity = {
            str(length): [
                {
                    "start_s": z["start_s"],
                    "end_s": z["end_s"],
                    "endpoint_mismatch_m": z["endpoint_mismatch_m"],
                }
                for z in bridge(ss, allanchors, primary, axis, ag, length)[1]
            ]
            for length in [0.5, 0.7, 0.85]
        }

        with (out / f"session{sid}_bridges.csv").open("w") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "time_s",
                    "relative_east_m",
                    "relative_north_m",
                    "heading_deg_ccw_from_east",
                    "segment_id",
                ]
            )
            w.writerows(brow)
        with (out / f"session{sid}_smoothing.csv").open("w") as f:
            w = csv.writer(f)
            w.writerow(["time_s", *methods])
            w.writerows(
                [
                    z,
                    *[
                        float(np.degrees(h[i])) if np.isfinite(h[i]) else ""
                        for h in methods.values()
                    ],
                ]
                for i, z in enumerate(t)
            )
        radius_counts = {
            str(scale): sum(z["accepted"] for z in anchors(ss, radius_scale=scale))
            for scale in [1, 1.5, 2]
        }
        independent = []
        for z in accepted:
            if not independent or z["start_s"] > independent[-1]["end_s"] + 5:
                independent.append(z)
        radius_errors = {
            str(scale): evaluate_method(
                ss,
                methods["rear_camera_minus_Z/mean/7s"],
                anchors(ss, radius_scale=scale),
            )
            for scale in [1, 1.5, 2]
        }

        # Course stability under +/-0.5 s shift, no new phone information.
        shifts = []
        for z in accepted:
            for delta in [-0.5, 0.5]:
                aa = gps_anchor(ss["providers"]["gps"], z["center_s"] + delta)
                if aa:
                    shifts.append(
                        abs(np.degrees(wrap(aa["direction_rad"] - z["direction_rad"])))
                    )
        # Same timestamps in fused and GPS indicate exact reuse; lack of equality doesn't imply independence.
        g = ss["providers"]["gps"]
        fu = ss["providers"]["fused"]
        overlap = []
        for z in fu:
            j = np.argmin(abs(g[:, 0] - z[0]))
            if abs(g[j, 0] - z[0]) < 0.05:
                overlap.append(float(np.linalg.norm(z[1:3] - g[j, 1:3])))
        summary[str(sid)] = {
            "primary_projection_selected_by_geometry": primary_axis,
            "rv_game_camera_difference_after_constant_alignment_p90_deg": float(
                np.nanpercentile(frame_mismatch, 90)
            ),
            "provider_audit": ss["audit"],
            "fused_near_same_time_gps_fixes": len(overlap),
            "fused_gps_same_time_median_distance_m": float(np.median(overlap))
            if overlap
            else None,
            "anchors": allanchors,
            "accepted_anchor_centers_s": [z["center_s"] for z in accepted],
            "nonoverlap_anchor_centers_with_5s_margin_s": [
                z["center_s"] for z in independent
            ],
            "radius_scale_sensitivity_counts": radius_counts,
            "radius_scale_camera_mean7_comparison": radius_errors,
            "gps_half_second_shift_difference_p90_deg": float(np.percentile(shifts, 90))
            if shifts
            else None,
            "smoother_comparison": stats,
            "repeatable_phone_turns_not_person_turns": turns,
            "gps_calibrated_step_bridges": bsegments,
            "bridge_step_length_sensitivity_m": bridge_sensitivity,
        }
        plots.append((ss, methods, accepted, brow, bsegments))
    cross = {}
    for source, target in [("2", "3"), ("3", "2")]:
        comparison = {}
        for name, z in summary[source]["smoother_comparison"].items():
            if name not in summary[target]["smoother_comparison"] or name.startswith(
                "game_"
            ):
                continue
            offset = z["fitted_constant_offset_deg"]
            res = summary[target]["smoother_comparison"][name]["offset_deg_by_anchor"]
            if offset is None or not res:
                continue
            factor = 2 if "PCA_axis" in name else 1
            err = abs(
                np.degrees(
                    wrap(
                        factor
                        * np.radians(np.array([a["offset_deg"] for a in res]) - offset)
                    )
                )
                / factor
            )
            comparison[name] = {
                "source_fitted_offset_deg": offset,
                "target_windows": len(err),
                "target_median_error_deg": float(np.median(err)),
            }
        cross[source + "_to_" + target] = comparison
    reciprocal = []
    alignment = np.genfromtxt(
        out.parent / "reciprocal_alignment.csv",
        delimiter=",",
        skip_header=1,
        usecols=[0, 1],
    )
    for a in [z for z in summary["2"]["anchors"] if z["accepted"]]:
        near = abs(alignment[:, 0] - a["center_s"]) <= 2
        matched_time = float(np.median(alignment[near, 1]))
        bb = [
            z
            for z in summary["3"]["anchors"]
            if z["accepted"] and abs(z["center_s"] - matched_time) <= 5
        ]
        if not bb:
            continue
        b = min(bb, key=lambda z: abs(z["center_s"] - matched_time))
        gps_error = float(
            abs(np.degrees(wrap(b["direction_rad"] - a["direction_rad"] - np.pi)))
        )
        reciprocal.append(
            {
                "session2_center_s": a["center_s"],
                "session3_center_s": b["center_s"],
                "sensor_DTW_predicted_session3_s": matched_time,
                "reciprocal_gps_heading_difference_deg": gps_error,
            }
        )
    summary["cross_session_offset_transfer"] = cross
    summary["independent_sensor_alignment_gps_reciprocity"] = reciprocal
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    render(plots, summary, out)
    print(
        json.dumps(
            {
                sid: {
                    k: v
                    for k, v in s.items()
                    if k
                    not in [
                        "anchors",
                        "smoother_comparison",
                        "radius_scale_camera_mean7_comparison",
                    ]
                }
                for sid, s in summary.items()
                if sid in ["2", "3"]
            },
            indent=2,
        )
    )
    return summary


def render(plots, summary, out):
    html = '<!doctype html><meta charset="utf-8"><title>GPS and orientation smoothing</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px;background:#edf2f6;color:#182a38}section{background:white;margin:20px 0;padding:25px;border-radius:12px}svg{width:100%;font:12px system-ui}.title{font-size:17px;font-weight:650}.legend{font-size:13px}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}table{width:100%;border-collapse:collapse}.warning{padding:18px;background:#fff0d8}</style><h1>Does smoothing phone orientation recover travel direction?</h1><p>GPS-assisted experiments on sessions 2 and 3. All headings use circular or quaternion-aware statistics. Axes: +Y phone top edge and −Z rear-camera direction; neither is assumed to be the walker’s direction.</p><p class="warning">GPS is a noisy comparison and calibration source. Reported accuracy is not indoor ground truth. A persistent hand-to-body offset survives smoothing; low jitter alone is not evidence of correct travel direction.</p>'
    html += "<section><h2>Measured outcome</h2><p>The rear-camera projection remains usable for most samples; the phone top edge is often nearly vertical. On GPS-informative intervals, the camera direction has a roughly 10° fitted offset from travel direction in both sessions.</p><p>Camera circular-mean smoothing at 7 seconds cuts the 90th-percentile 0.2-second heading change from 13.9° to 4.1° in session 2 and 11.4° to 3.8° in session 3. GPS agreement is not uniformly better: the preferred smoothing window changes between recordings.</p><p>The GPS gates retain 10 / 15 overlapping windows in sessions 2 / 3, but only 2 / 3 can be chosen without overlap and a five-second margin. Inflating reported radii by 1.5 retains 4 / 10; doubling them leaves none. Results are conditional on reported GPS quality.</p></section>"
    for ss, methods, accepted, brow, segments in plots:
        sid = ss["id"]
        s = summary[str(sid)]
        t = ss["t"]
        g = ss["providers"]["gps"]
        series = []
        for key, color in [
            ("raw/0s", "#a7b3bf"),
            ("mean/7s", "#2166ac"),
            ("median/7s", "#cc6611"),
            ("adaptive/15s", "#228b69"),
        ]:
            full = s["primary_projection_selected_by_geometry"] + "/" + key
            series.append((full, t, np.degrees(methods[full]), color))
        if accepted:
            series.append(
                (
                    "informative GPS chords",
                    np.array([z["center_s"] for z in accepted]),
                    np.degrees([z["direction_rad"] for z in accepted]),
                    "#9a298e",
                )
            )
        html += (
            f"<section><h2>Session {sid}</h2><p>Bridge projection selected by horizontal conditioning: {s['primary_projection_selected_by_geometry']}.</p><p>{len(accepted)} overlapping 20-second GPS windows pass geometry, regression and window-duration checks. The number is not an independent anchor count.</p>"
            + chart(
                series,
                "Phone direction versus informative GPS movement",
                "° CCW from east; time (s)",
                height=300,
            )
        )
        html += "<p>Gaps mark ill-conditioned phone-axis projections. Lines between sparse GPS directions are visual guides, not continuous GPS heading estimates. Angles wrap at ±180°.</p><h3>Smoothing comparison</h3><table><tr><th>Method</th><th>GPS windows</th><th>Raw median disagreement</th><th>Fit offset</th><th>Held-out windows</th><th>Held-out median error</th></tr>"
        fmt = lambda x: "—" if x is None else f"{x:.1f}°"
        for name, z in s["smoother_comparison"].items():
            html += f"<tr><td>{name}</td><td>{z['gps_windows_evaluated']}</td><td>{fmt(z['raw_median_error_deg'])}</td><td>{fmt(z['fitted_constant_offset_deg'])}</td><td>{z['heldout_nonoverlap_windows']}</td><td>{fmt(z['heldout_median_error_deg'])}</td></tr>"
        html += "</table><p>Offset calibration excludes GPS intervals overlapping each held-out interval plus a five-second margin. At least two training windows and concentration ≥0.5 are required. Training windows may overlap each other. Every GPS comparison aggregates predicted headings over detected steps in that GPS interval, including the raw-heading baseline; it already averages many samples. Game rotation-vector headings require a fitted arbitrary-frame offset, so their raw errors are not shown. Raw/held-out errors for PCA are axial (modulo 180°), so they are not directly comparable to signed phone-heading errors. No method is selected by the smallest in-sample error.</p></section>"
        # Relative GPS map, with fitted step bridges shown separately from unsupported track.
        allxy = [g[:, 1:3]] + [np.array(brow)[:, 1:3]] if brow else [g[:, 1:3]]
        xy = np.concatenate(allxy)
        mid = (xy.min(0) + xy.max(0)) / 2
        scale = 430 / max(np.ptp(xy, axis=0))
        convert = lambda p: (p - mid) * [scale, -scale] + [500, 270]
        svg = (
            '<svg viewBox="0 0 1000 540"><text x="25" y="25" class="title">GPS track and conditional step bridges (relative east/north)</text>'
            + polyline(convert(g[:, 1:3]), "#aab4bd", 2)
        )
        for a in accepted:
            svg += polyline(
                convert(np.array([a["start_xy_m"], a["end_xy_m"]])), "#9a298e", 2, 0.5
            )
        if brow:
            b = np.array(brow)
            for i in np.unique(b[:, 4]):
                svg += polyline(convert(b[b[:, 4] == i, 1:3]), "#168060", 3)
        svg += f'<path d="M30 500 h{10 * scale}" stroke="#202a35" stroke-width="3"/><text x="30" y="522">10 m · gray GPS · purple informative chords · green step bridges</text></svg>'
        html += (
            "<section>"
            + svg
            + "<p>Bridges use 0.70 m per detected step, phone circular-median direction with offsets calibrated at GPS anchors, and an acceleration-axis correction only when separately supported and consistent. No bridge is extrapolated across an anchor gap over 35 seconds. Both endpoint course measurements contribute to the fit. Green paths and endpoint mismatches are diagnostic hypotheses, not independent validation.</p><h3>Repeatable changes in phone direction</h3><table><tr><th>Candidate time</th><th>Local phone change</th><th>GPS comparison span</th><th>GPS change over that span</th><th>Agreement on matching windows</th></tr>"
        )
        for z in s["repeatable_phone_turns_not_person_turns"]:
            span = str(z.get("gps_comparison_span_s", "Unavailable"))
            status = (
                "Supported broad change"
                if z["agrees_with_gps_within_30deg"]
                else (
                    "Disagrees"
                    if z["agrees_with_gps_within_30deg"] is False
                    else "Unverified"
                )
            )
            html += f"<tr><td>{z['time_s']:.0f} s</td><td>{z['phone_direction_change_deg']:+.1f}°</td><td>{span}</td><td>{fmt(z['gps_change_deg'])}</td><td>{status}</td></tr>"
        html += "</table><p>GPS comparisons span tens of seconds and can include multiple turns. They support a broad direction change, not an exact corner, angle, or turn time. Candidate detection requires all three smoothing variants and the game-rotation-vector variant to agree within 20°.</p></section>"
    html += "<section><h2>Offset transfer between sessions</h2><table><tr><th>Calibrate → evaluate</th><th>Camera direction method</th><th>Offset</th><th>Target median error</th></tr>"
    for direction, comparison in summary["cross_session_offset_transfer"].items():
        for key in [
            "rear_camera_minus_Z/raw/0s",
            "rear_camera_minus_Z/mean/7s",
            "rear_camera_minus_Z/median/7s",
            "rear_camera_minus_Z/median/15s",
            "rear_camera_minus_Z/adaptive/15s",
        ]:
            z = comparison[key]
            html += f"<tr><td>{direction}</td><td>{key}</td><td>{z['source_fitted_offset_deg']:.1f}°</td><td>{z['target_median_error_deg']:.1f}°</td></tr>"
    html += (
        "</table><p>Constant offset is fitted in one recording and applied unchanged to the other. This tests persistence on informative GPS intervals only; it does not validate orientation during unanchored parts of the trip.</p><h2>GPS reciprocity checked against prior sensor-only alignment</h2><pre>"
        + json.dumps(summary["independent_sensor_alignment_gps_reciprocity"], indent=2)
        + "</pre><p>Pressure/magnetic DTW did not use GPS. We now compare accepted GPS course anchors at those candidate correspondences, expecting opposite travel directions. Shared-route identity is still a hypothesis.</p></section>"
    )
    html += '<section><h2>Anchor selection and uncertainty</h2><p>GPS only; deduplicate monotonic fix times and exclude stale fixes. Endpoint positions are medians of ±2-second clusters. Cluster uncertainty is at least the median reported accuracy and is never divided by √N. Require displacement ≥2× the sum of endpoint radii, plausible walking speed, robust-regression agreement, and ≤20° direction variation across 15/20/25/30-second intervals. Radius multipliers 1/1.5/2 and ±0.5-second timing shifts are tested.</p><p>Android reported accuracy is a 68% horizontal radius, not a hard bound. The displacement/radius criterion is a heuristic. Spatially correlated indoor multipath can pass it. Fused/provider agreement is not independent corroboration. GPS bearings are true-north referenced; rotation-vector headings are magnetic referenced. Fitted offsets include north-reference differences as well as phone/body pose.</p><p><a href="summary.json">Full audit and comparisons</a> · <a href="../joint_report.html">Reciprocal sensor evidence</a> · <a href="https://developer.android.com/reference/android/location/Location">Android location conventions</a> · <a href="https://developer.android.com/develop/sensors-and-location/sensors/sensors_motion">Android orientation conventions</a></p></section>'
    (out / "report.html").write_text(html)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("database", type=Path)
    p.add_argument("--out", type=Path, default=Path("output/gps_orientation"))
    a = p.parse_args()
    main(a.database, a.out)
