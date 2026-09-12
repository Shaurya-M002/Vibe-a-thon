import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { footprint } from "./geometry.js";
const d = JSON.parse(
  fs.readFileSync(new URL("./model-data.json", import.meta.url)),
);
test("geographic axes preserve north, east and positive height", () => {
  const g = footprint(
    [
      [0, 0],
      [10, 0],
      [10, -20],
      [0, -20],
    ],
    60,
  );
  g.computeBoundingBox();
  assert.equal(g.boundingBox.min.x, 0);
  assert.equal(g.boundingBox.max.x, 10);
  assert.ok(Math.abs(g.boundingBox.max.y - 60) < 1e-6);
  assert.equal(g.boundingBox.min.z, -20);
});
test("both actual OSM outlines produce finite geometry", () => {
  const main = d.buildings.filter((b) => b.main);
  assert.equal(main.length, 2);
  for (const b of main) {
    assert.equal(b.tags["building:levels"], "20");
    assert.ok(b.points.length > 20);
    const g = footprint(b.points, 60);
    assert.ok(Array.from(g.attributes.position.array).every(Number.isFinite));
    assert.ok(g.attributes.position.count > 100);
  }
});
import {
  pathSurface,
  polylineLength,
  internalRoads,
  heightEstimate,
} from "./paths.js";
test("road surface follows a bent centreline without moving its vertices", () => {
  const p = [
      [0, 0],
      [10, 0],
      [10, 10],
    ],
    g = pathSurface(p, 6);
  assert.equal(polylineLength(p), 20);
  const a = Array.from(g.attributes.position.array);
  assert.ok(a.every(Number.isFinite));
  for (const [x, z] of p) {
    assert.ok(
      a.some((v, i) => i % 3 === 0 && v === x && Math.abs(a[i + 2] - z) < 1e-6),
    );
  }
  g.computeBoundingBox();
  assert.ok(g.boundingBox.min.x >= -3.001);
  assert.ok(g.boundingBox.max.x <= 13.001);
});
test("height is based on the selected count and interval", () => {
  assert.equal(heightEstimate(20, 3), 60);
  assert.equal(heightEstimate(19, 3.2), 60.800000000000004);
  assert.equal(heightEstimate(20, 2.8), 56);
  assert.equal(heightEstimate(20, 3.3), 66);
});
test("internal path selection retains both phases and source bend coordinates", () => {
  const roads = internalRoads(d);
  assert.ok(roads.some((f) => f.tags.name?.includes("Elita B")));
  assert.ok(roads.some((f) => f.id === 1102763849));
  for (const f of roads)
    assert.deepEqual(f.points, d.context.find((c) => c.id === f.id).points);
});

import { clearanceWidth, distanceToFootprints } from "./paths.js";
test("display width respects building clearance along the entire road surface", () => {
  const polygons = d.buildings.filter((b) => b.main).map((b) => b.points);
  let limited = 0;
  for (const road of internalRoads(d)) {
    const w = clearanceWidth(road.points, 6, polygons);
    assert.ok(w >= 0 && w <= 6);
    if (w < 6) limited++;
    const g = pathSurface(road.points, w),
      v = g.attributes.position;
    for (let i = 0; i < v.count; i++)
      assert.ok(
        distanceToFootprints([v.getX(i), v.getZ(i)], polygons) > 0.001,
        "rendered road vertex is inside a building",
      );
  }
  assert.ok(limited > 0);
});
test("clearance includes endpoints and refuses a road inside a footprint", () => {
  const polygons = [
    [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
    ],
  ];
  assert.equal(
    clearanceWidth(
      [
        [2, 2],
        [8, 2],
      ],
      6,
      polygons,
    ),
    0,
  );
  assert.ok(
    clearanceWidth(
      [
        [-2, -5],
        [-2, 15],
      ],
      6,
      polygons,
    ) < 4,
  );
});

import { unitPlan, buildInterior, planPoint } from "./interior-plan.js";
test("interior plan remains unassigned and its south entry is an actual opening", () => {
  assert.equal(unitPlan.status.tower, null);
  assert.equal(unitPlan.status.floor, null);
  assert.equal(unitPlan.status.sharedHallway, "unknown");
  const [ex, ez] = planPoint(unitPlan.door);
  assert.equal(unitPlan.door[1], 633);
  for (const w of unitPlan.walls) {
    const a = planPoint(w.slice(0, 2)),
      b = planPoint(w.slice(2));
    if (Math.abs(a[1] - ez) < 0.01 && Math.abs(b[1] - ez) < 0.01)
      assert.ok(ex < Math.min(a[0], b[0]) || ex > Math.max(a[0], b[0]));
  }
  for (const mode of [true, false]) {
    const g = buildInterior(mode);
    g.traverse((o) => {
      if (o.geometry)
        assert.ok(
          Array.from(o.geometry.attributes.position.array).every(
            Number.isFinite,
          ),
        );
    });
  }
});

test("en-suite opens directly to master bedroom and has no passage-side door", () => {
  assert.ok(unitPlan.walls.some((w) => w.join(",") === "225,359,383,359"));
  assert.ok(
    !unitPlan.walls.some(
      (w) =>
        w[0] === 225 &&
        w[2] === 225 &&
        Math.min(w[1], w[3]) < 438 &&
        Math.max(w[1], w[3]) > 438,
    ),
  );
});
