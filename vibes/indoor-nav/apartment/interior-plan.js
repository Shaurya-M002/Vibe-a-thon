import { furnishInterior } from "./interior-furniture.js?v=21";
import * as THREE from "three";
import { flat } from "./geometry.js";
// User sketch defines room connections; dimensions and furniture are schematic.
export const unitPlan = {
  id: "user-sketch-three-bedroom",
  title: "Three-bedroom apartment",
  source: "User-provided sketch",
  pixelsPerMetre: 57.3,
  wallHeight: 2.7,
  wallThickness: 0.14,
  shell: [
    [20, 102],
    [891, 102],
    [891, 633],
    [20, 633],
  ],
  walls: [
    [20, 102, 891, 102],
    [891, 102, 891, 633],
    [20, 102, 20, 633],
    [20, 633, 812, 633],
    [872, 633, 891, 633],
    [274, 102, 274, 304],
    [477, 102, 477, 298],
    [20, 304, 224, 304],
    [324, 298, 477, 298],
    [225, 359, 383, 359],
    [225, 359, 225, 413],
    [225, 463, 225, 633],
    [383, 359, 383, 633],
    [433, 359, 521, 359],
    [521, 359, 521, 633],
    [521, 359, 590, 359],
    [590, 359, 590, 439],
    [590, 489, 590, 633],
    [712, 359, 787, 359],
    [787, 359, 787, 633],
  ],
  parapets: [],
  windows: [],
  rooms: [
    {
      name: "Bedroom 3",
      label: [145, 205],
      polygon: [
        [20, 102],
        [274, 102],
        [274, 304],
        [20, 304],
      ],
      color: 0xe2ded5,
    },
    {
      name: "Bedroom 2",
      label: [377, 205],
      polygon: [
        [274, 102],
        [477, 102],
        [477, 298],
        [274, 298],
      ],
      color: 0xe2ded5,
    },
    {
      name: "Master bedroom",
      label: [122, 525],
      polygon: [
        [20, 304],
        [225, 304],
        [225, 633],
        [20, 633],
      ],
      color: 0xe2ded5,
    },
    {
      name: "En-suite",
      label: [304, 545],
      polygon: [
        [225, 359],
        [383, 359],
        [383, 633],
        [225, 633],
      ],
      color: 0xccd6d3,
    },
    {
      name: "Bath",
      label: [452, 545],
      polygon: [
        [383, 359],
        [521, 359],
        [521, 633],
        [383, 633],
      ],
      color: 0xccd6d3,
    },
    {
      name: "Utility",
      label: [555, 545],
      polygon: [
        [521, 359],
        [590, 359],
        [590, 633],
        [521, 633],
      ],
      color: 0xccd6d3,
    },
    {
      name: "Kitchen",
      label: [687, 530],
      polygon: [
        [590, 359],
        [787, 359],
        [787, 633],
        [590, 633],
      ],
      color: 0xccd6d3,
    },
    {
      name: "Entry",
      label: [839, 530],
      polygon: [
        [787, 359],
        [891, 359],
        [891, 633],
        [787, 633],
      ],
      color: 0xe2ded5,
    },
    {
      name: "Living / dining",
      label: [687, 235],
      polygon: [
        [477, 102],
        [891, 102],
        [891, 359],
        [477, 359],
      ],
      color: 0xe2ded5,
    },
  ],
  door: [842, 633],
  doorSwings: [
    [224, 304, 50, -1],
    [324, 298, 50, 1, Math.PI],
    [225, 413, 50, -1, Math.PI / 2],
    [433, 359, 50, -1, Math.PI],
    [590, 439, 50, 1, Math.PI / 2],
    [812, 633, 60, -1],
  ],
  calibration: [],
  status: {
    tower: null,
    floor: null,
    sharedHallway: "unknown",
    registration: "unassigned",
  },
};
export function planPoint(p) {
  return [
    (p[0] - 455.5) / unitPlan.pixelsPerMetre,
    (p[1] - 367.5) / unitPlan.pixelsPerMetre,
  ];
}
export function buildInterior(cutaway = true, furnished = true) {
  const g = new THREE.Group();
  g.name = "User-corrected three-bedroom apartment";
  const mats = {
    wall: new THREE.MeshStandardMaterial({ color: 0xe0ddd1, roughness: 0.92 }),
    slab: new THREE.MeshStandardMaterial({
      color: 0xe2ded5,
      side: THREE.DoubleSide,
      roughness: 1,
    }),
    glass: new THREE.MeshStandardMaterial({
      color: 0x86b3c0,
      transparent: true,
      opacity: 0.55,
      side: THREE.DoubleSide,
    }),
    door: new THREE.MeshStandardMaterial({ color: 0x74c6a4, roughness: 0.8 }),
  };
  const floor = new THREE.Mesh(flat(unitPlan.shell.map(planPoint)), mats.slab);
  floor.position.y = 0.015;
  g.add(floor);
  for (const r of unitPlan.rooms) {
    const f = new THREE.Mesh(
      flat(r.polygon.map(planPoint)),
      new THREE.MeshStandardMaterial({
        color: r.color,
        side: THREE.DoubleSide,
        roughness: 1,
      }),
    );
    f.position.y = 0.025;
    g.add(f);
  }
  function wall(v, h, mat, y = 0) {
    const a = planPoint(v.slice(0, 2)),
      b = planPoint(v.slice(2)),
      dx = b[0] - a[0],
      dz = b[1] - a[1];
    const m = new THREE.Mesh(
      new THREE.BoxGeometry(Math.hypot(dx, dz), h, unitPlan.wallThickness),
      mat,
    );
    m.position.set((a[0] + b[0]) / 2, y + h / 2, (a[1] + b[1]) / 2);
    m.rotation.y = -Math.atan2(dz, dx);
    g.add(m);
    if (cutaway) {
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(m.geometry),
        new THREE.LineBasicMaterial({
          color: 0x52615c,
          transparent: true,
          opacity: 0.48,
        }),
      );
      edges.position.copy(m.position);
      edges.rotation.copy(m.rotation);
      g.add(edges);
    }
  }
  for (const w of unitPlan.walls) {
    const horizontal = w[1] === w[3],
      axis = horizontal ? 0 : 1,
      fixed = horizontal ? 1 : 0;
    const lo = Math.min(w[axis], w[axis + 2]),
      hi = Math.max(w[axis], w[axis + 2]);
    const gaps = unitPlan.windows
      .filter((v) => v[fixed] === w[fixed] && v[fixed + 2] === w[fixed])
      .map((v) => [
        Math.max(lo, Math.min(v[axis], v[axis + 2])),
        Math.min(hi, Math.max(v[axis], v[axis + 2])),
      ])
      .filter((v) => v[1] > v[0])
      .sort((a, b) => a[0] - b[0]);
    const run = (a, b, h, y = 0) => {
      if (b <= a) return;
      wall(
        horizontal ? [a, w[1], b, w[1]] : [w[0], a, w[0], b],
        h,
        mats.wall,
        y,
      );
    };
    const height = cutaway ? 1.05 : unitPlan.wallHeight;
    let cursor = lo;
    for (const [a, b] of gaps) {
      run(cursor, a, height);
      run(a, b, 0.7);
      if (height > 2.05) run(a, b, height - 2.05, 2.05);
      cursor = b;
    }
    run(cursor, hi, height);
  }
  for (const w of unitPlan.parapets) wall(w, 0.7, mats.wall);
  for (const w of unitPlan.windows)
    wall(w, cutaway ? 0.35 : 1.15, mats.glass, cutaway ? 0.7 : 0.9);
  const entry = planPoint(unitPlan.door);
  const threshold = new THREE.Mesh(
    new THREE.BoxGeometry(1.25, 0.08, 0.22),
    mats.door,
  );
  threshold.position.set(entry[0], 0.07, entry[1]);
  g.add(threshold);
  if (cutaway)
    for (const [x, y, width, sign, rotation = 0] of unitPlan.doorSwings) {
      const points = [];
      for (let i = 0; i <= 20; i++) {
        const angle = rotation + (sign * i * Math.PI) / 40,
          p = planPoint([
            x + Math.cos(angle) * width,
            y + Math.sin(angle) * width,
          ]);
        points.push(new THREE.Vector3(p[0], 0.085, p[1]));
      }
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({
          color: 0x70877c,
          transparent: true,
          opacity: 0.7,
        }),
      );
      g.add(line);
    }
  if (furnished) g.add(furnishInterior(planPoint, unitPlan.pixelsPerMetre));
  if (typeof document !== "undefined")
    for (const r of unitPlan.rooms) {
      const canvas = document.createElement("canvas"),
        measure = canvas.getContext("2d");
      measure.font = "500 30px sans-serif";
      canvas.width = Math.ceil(measure.measureText(r.name).width) + 24;
      canvas.height = 64;
      const c = canvas.getContext("2d");
      c.fillStyle = "#30463e";
      c.font = "500 30px sans-serif";
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(r.name, canvas.width / 2, 32);
      const tex = new THREE.CanvasTexture(canvas),
        m = new THREE.Mesh(
          new THREE.PlaneGeometry((canvas.width / 64) * 0.7, 0.7),
          new THREE.MeshBasicMaterial({
            map: tex,
            transparent: true,
            depthWrite: false,
            side: THREE.DoubleSide,
          }),
        );
      const p = planPoint(r.label);
      m.rotation.x = -Math.PI / 2;
      m.position.set(p[0], 0.07, p[1]);
      g.add(m);
    }

  return g;
}
