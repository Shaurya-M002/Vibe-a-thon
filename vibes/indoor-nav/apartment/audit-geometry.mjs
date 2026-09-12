import fs from "node:fs";
import {
  internalRoads,
  pointInside,
  pathSurface,
  clearanceWidth,
} from "./paths.js";
const d = JSON.parse(
    fs.readFileSync(new URL("./model-data.json", import.meta.url)),
  ),
  polygons = d.buildings.filter((b) => b.main).map((b) => b.points);
const inside = (p) => polygons.some((poly) => pointInside(p, poly));
const ways = [];
for (const road of internalRoads(d)) {
  const width = clearanceWidth(road.points, 6, polygons);
  let centerHits = 0,
    rawEdgeHits = 0,
    total = 0;
  for (let i = 1; i < road.points.length; i++) {
    const a = road.points[i - 1],
      b = road.points[i],
      dx = b[0] - a[0],
      dz = b[1] - a[1],
      len = Math.hypot(dx, dz);
    if (!len) continue;
    const n = Math.ceil(len);
    for (let j = 0; j < n; j++) {
      const t = (j + 0.5) / n,
        p = [a[0] + t * dx, a[1] + t * dz];
      total++;
      if (inside(p)) centerHits++;
      for (const sign of [-1, 1])
        if (
          inside([
            p[0] - ((sign * dz) / len) * 3,
            p[1] + ((sign * dx) / len) * 3,
          ])
        )
          rawEdgeHits++;
    }
  }
  const g = pathSurface(road.points, width),
    v = g.attributes.position;
  let renderedVertexHits = 0;
  for (let i = 0; i < v.count; i++)
    if (inside([v.getX(i), v.getZ(i)])) renderedVertexHits++;
  ways.push({
    way: road.id,
    centerSamples: total,
    centerHits,
    rawSixMetreEdgeHits: rawEdgeHits,
    displayWidthM: width,
    renderedVertexHits,
  });
  g.dispose();
}
const out = {
  method:
    "Center/edge midpoint checks at <=1m; all rendered vertices checked. Display width conservatively bounded using footprint distance sampled at <=0.5m with Lipschitz margin. This is map consistency, not a real-width measurement or navigation validation.",
  centerHits: ways.reduce((s, w) => s + w.centerHits, 0),
  rawSixMetreEdgeHits: ways.reduce((s, w) => s + w.rawSixMetreEdgeHits, 0),
  renderedVertexHits: ways.reduce((s, w) => s + w.renderedVertexHits, 0),
  clampedWays: ways.filter((w) => w.displayWidthM < 6).length,
  ways,
};
fs.writeFileSync(
  new URL("./geometry-audit.json", import.meta.url),
  JSON.stringify(out, null, 2),
);
console.log({
  centerHits: out.centerHits,
  rawSixMetreEdgeHits: out.rawSixMetreEdgeHits,
  renderedVertexHits: out.renderedVertexHits,
  clampedWays: out.clampedWays,
});
