import * as THREE from "three";
export function footprint(points, height) {
  const s = new THREE.Shape();
  points.forEach((p, i) => (i ? s.lineTo(p[0], -p[1]) : s.moveTo(p[0], -p[1])));
  s.closePath();
  const g = new THREE.ExtrudeGeometry(s, {
    depth: height,
    bevelEnabled: false,
    steps: 1,
  });
  g.rotateX(-Math.PI / 2);
  return g;
}
export function flat(points) {
  const s = new THREE.Shape();
  points.forEach((p, i) => (i ? s.lineTo(p[0], -p[1]) : s.moveTo(p[0], -p[1])));
  s.closePath();
  const g = new THREE.ShapeGeometry(s);
  g.rotateX(-Math.PI / 2);
  return g;
}
