"""Heuristic vertical events and radio similarity; neither establishes position."""

import numpy as np


def vertical_candidates(t, h, steps, threshold=2.0):
    """Compare 3-second median plateaus over 12s; group neighboring detections."""
    candidates = []
    for a in range(2, int(t[-1]) - 13):
        left = h[(t >= a - 2) & (t <= a)]
        right = h[(t >= a + 10) & (t <= a + 12)]
        if not len(left) or not len(right):
            continue
        delta = float(np.median(right) - np.median(left))
        if abs(delta) >= threshold:
            n = int(((steps >= a) & (steps <= a + 12)).sum())
            candidates.append(
                dict(
                    start=a,
                    end=a + 12,
                    delta=round(delta, 2),
                    steps=n,
                    kind="lift-like" if n <= 2 else "walking + height change",
                )
            )
    groups = []
    for e in candidates:
        if (
            groups
            and e["start"] <= groups[-1][-1]["end"]
            and np.sign(e["delta"]) == np.sign(groups[-1][-1]["delta"])
        ):
            groups[-1].append(e)
        else:
            groups.append([e])
    return [max(g, key=lambda e: abs(e["delta"])) for g in groups]


def fingerprint_score(a, b):
    common = set(a) & set(b)
    union = set(a) | set(b)
    return (
        len(common) / len(union) if union else 0,
        float(np.median([abs(a[k] - b[k]) for k in common])) if common else None,
        len(common),
    )
