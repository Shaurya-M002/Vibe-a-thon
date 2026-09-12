import * as THREE from "three";
// Preserve mapped centreline vertices; round the joins instead of replacing
// the surveyed/mapped polyline with an unconstrained decorative spline.
export function pathSurface(points, width, y = 0.12) {
  const vertices = [],
    indices = [];
  const add = (x, z) => {
    vertices.push(x, y, z);
    return vertices.length / 3 - 1;
  };
  const tri = (a, b, c) => indices.push(a, b, c);
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      dx = b[0] - a[0],
      dz = b[1] - a[1],
      len = Math.hypot(dx, dz);
    if (len < 1e-6) continue;
    const nx = ((-dz / len) * width) / 2,
      nz = ((dx / len) * width) / 2;
    const ids = [
      add(a[0] + nx, a[1] + nz),
      add(b[0] + nx, b[1] + nz),
      add(b[0] - nx, b[1] - nz),
      add(a[0] - nx, a[1] - nz),
    ];
    tri(ids[0], ids[1], ids[2]);
    tri(ids[0], ids[2], ids[3]);
  }
  for (const p of points) {
    const c = add(...p);
    for (let i = 0; i < 20; i++) {
      const a = (i * Math.PI) / 10,
        b = ((i + 1) * Math.PI) / 10;
      tri(
        c,
        add(p[0] + (Math.cos(a) * width) / 2, p[1] + (Math.sin(a) * width) / 2),
        add(p[0] + (Math.cos(b) * width) / 2, p[1] + (Math.sin(b) * width) / 2),
      );
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  return g;
}
export const polylineLength = (p) =>
  p
    .slice(1)
    .reduce((s, b, i) => s + Math.hypot(b[0] - p[i][0], b[1] - p[i][1]), 0);
export function pointInside(p, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i],
      b = polygon[j];
    if (
      a[1] > p[1] !== b[1] > p[1] &&
      p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  }
  return inside;
}
export function internalRoads(data) {
  const boundaries = data.context.filter(
    (f) => f.tags.landuse === "residential" && f.tags.name?.includes("Elita"),
  );
  return data.context.filter(
    (f) =>
      f.tags.highway &&
      (f.tags.name?.includes("Elita") ||
        f.points.every((p) =>
          boundaries.some((b) => pointInside(p, b.points)),
        )),
  );
}
export function heightEstimate(levels, storey, roof = 0) {
  return levels * storey + roof;
}

export function distanceToFootprints(p, polygons) {
  let best = Infinity;
  for (const polygon of polygons) {
    if (pointInside(p, polygon)) return 0;
    for (let i = 0; i < polygon.length; i++) {
      const a = polygon[i],
        b = polygon[(i + 1) % polygon.length],
        dx = b[0] - a[0],
        dz = b[1] - a[1],
        den = dx * dx + dz * dz;
      const u = den
        ? Math.max(
            0,
            Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / den),
          )
        : 0;
      best = Math.min(
        best,
        Math.hypot(p[0] - a[0] - u * dx, p[1] - a[1] - u * dz),
      );
    }
  }
  return best;
}
// Conservative display clearance, not a measurement of real driveway width.
// Distance to a closed set is 1-Lipschitz: subtract half the sampling gap
// to bound clearance continuously between samples, including round caps.
export function clearanceWidth(points, requested, polygons, spacing = 0.5) {
  let clearance = Infinity;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      length = Math.hypot(b[0] - a[0], b[1] - a[1]),
      n = Math.max(1, Math.ceil(length / spacing));
    for (let j = 0; j <= n; j++) {
      const t = j / n,
        p = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      clearance = Math.min(
        clearance,
        distanceToFootprints(p, polygons) - length / n / 2,
      );
    }
  }
  return Math.max(0, Math.min(requested, 2 * Math.max(0, clearance - 0.03)));
}
