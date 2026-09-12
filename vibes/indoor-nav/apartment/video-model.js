import * as THREE from "three";
// Video-informed appearance only. No feature positions or dimensions are surveyed.
export function facadeLayer(points, levels, storey) {
  const group = new THREE.Group(),
    canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const c = canvas.getContext("2d");
  c.fillStyle = "#e4e3dc";
  c.fillRect(0, 0, 128, 128);
  c.fillStyle = "#687476";
  c.fillRect(15, 18, 98, 91);
  c.fillStyle = "#38494d";
  c.fillRect(19, 22, 90, 58);
  c.fillStyle = "#9eacab";
  c.fillRect(19, 82, 90, 22);
  c.fillStyle = "#e9e7df";
  c.fillRect(9, 110, 110, 12);
  c.fillStyle = "#c1c6c0";
  for (let x = 20; x < 113; x += 15) c.fillRect(x, 80, 2, 26);
  c.fillRect(17, 79, 96, 3);
  const windowCanvas = document.createElement("canvas");
  windowCanvas.width = 128;
  windowCanvas.height = 128;
  const w = windowCanvas.getContext("2d");
  w.fillStyle = "#e4e3dc";
  w.fillRect(0, 0, 128, 128);
  w.fillStyle = "#56686c";
  w.fillRect(39, 28, 50, 64);
  w.fillStyle = "#bcc4c0";
  w.fillRect(62, 28, 3, 64);
  w.fillRect(39, 60, 50, 3);
  const windows = new THREE.CanvasTexture(windowCanvas);
  windows.colorSpace = THREE.SRGBColorSpace;
  windows.wrapS = windows.wrapT = THREE.RepeatWrapping;
  const base = new THREE.CanvasTexture(canvas);
  base.colorSpace = THREE.SRGBColorSpace;
  base.wrapS = base.wrapT = THREE.RepeatWrapping;
  const area = points.reduce((a, p, i) => {
    const q = points[(i + 1) % points.length];
    return a + p[0] * q[1] - q[0] * p[1];
  }, 0);
  for (let i = 0; i < points.length; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length],
      dx = b[0] - a[0],
      dz = b[1] - a[1],
      len = Math.hypot(dx, dz);
    if (len < 4) continue;
    const tex = (len < 9 ? windows : base).clone();
    tex.needsUpdate = true;
    tex.repeat.set(Math.max(1, Math.round(len / 4)), levels - 1);
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(len, storey * (levels - 1)),
      new THREE.MeshStandardMaterial({
        map: tex,
        roughness: 0.94,
        side: THREE.DoubleSide,
      }),
    );
    const sign = area > 0 ? 1 : -1;
    mesh.position.set(
      (a[0] + b[0]) / 2 + ((sign * dz) / len) * 0.04,
      storey + ((levels - 1) * storey) / 2,
      (a[1] + b[1]) / 2 - ((sign * dx) / len) * 0.04,
    );
    mesh.rotation.y = -Math.atan2(dz, dx);
    group.add(mesh);
  }
  base.dispose();
  windows.dispose();
  return group;
}
export function detailModel(kind) {
  const g = new THREE.Group();
  g.name = "Video-informed " + kind + " study — dimensions illustrative";
  const mats = {
    stone: new THREE.MeshStandardMaterial({ color: 0xb5aa93, roughness: 0.95 }),
    wall: new THREE.MeshStandardMaterial({ color: 0xe4e1d4 }),
    green: new THREE.MeshStandardMaterial({ color: 0x516c39, roughness: 1 }),
    metal: new THREE.MeshStandardMaterial({
      color: 0xa7b7ba,
      metalness: 0.7,
      roughness: 0.3,
    }),
    dark: new THREE.MeshStandardMaterial({ color: 0x65716b }),
  };
  function box(x, y, z, w, h, d, mat) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mats[mat]);
    m.position.set(x, y, z);
    g.add(m);
    return m;
  }
  function rod(a, b, r = 0.035) {
    const p = new THREE.Vector3(...a),
      q = new THREE.Vector3(...b),
      delta = q.clone().sub(p);
    const m = new THREE.Mesh(
      new THREE.CylinderGeometry(r, r, delta.length(), 8),
      mats.metal,
    );
    m.position.copy(p).add(q).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      delta.normalize(),
    );
    g.add(m);
  }

  if (kind === "entrance") {
    box(0, 0.01, 1, 1.7, 0.14, 8, "stone");
    box(0, 0.02, -4, 6, 0.16, 3, "dark");
    for (const x of [-1.15, 1.15]) {
      box(x, 1.7, -3, 0.45, 3.4, 0.5, "wall");
      box(x * 2.2, 1.7, -4, 0.45, 3.4, 2.5, "wall");
    }
    box(0, 3.45, -3.8, 6, 0.35, 3.6, "wall");
    for (const x of [-0.86, 0.86]) {
      for (const z of [-2.6, -1.6, -0.6]) rod([x, 0.1, z], [x, 1.13, z]);
      for (const y of [0.4, 0.73, 1.12]) rod([x, y, -2.75], [x, y, -0.45]);
    }
    for (const side of [-1, 1])
      for (let j = 0; j < 6; j++) {
        const m = new THREE.Mesh(
          new THREE.IcosahedronGeometry(0.48, 1),
          mats.green,
        );
        m.position.set(side * 1.5, 0.5, j * 0.95 - 1.8);
        m.scale.set(1, 0.8, 1);
        g.add(m);
      }
  } else {
    // Curved slab sequence visible in s3 5–15s; shape and scale are a local study.
    for (let i = 0; i < 10; i++) {
      const z = 4.5 - i * 0.88,
        x = 0.9 * Math.sin(i * 0.28);
      const m = box(x, 0.05, z, 0.94, 0.12, 0.7, "stone");
      m.rotation.y = 0.2 * Math.cos(i * 0.28);
    }

    for (let i = 0; i < 8; i++) {
      const m = new THREE.Mesh(
        new THREE.IcosahedronGeometry(0.5, 1),
        mats.green,
      );
      m.position.set(2, 0.5, 3 - i * 0.9);
      g.add(m);
    }
  }
  return g;
}

export function roofFrames(points, height) {
  const group = new THREE.Group();
  group.name = "Photo-informed roof frames — dimensions approximate";
  const mat = new THREE.MeshStandardMaterial({
    color: 0xdadbd3,
    roughness: 0.87,
  });
  const area = points.reduce((a, p, i) => {
    const q = points[(i + 1) % points.length];
    return a + p[0] * q[1] - q[0] * p[1];
  }, 0);
  function beam(a, b, width = 0.32) {
    const p = new THREE.Vector3(...a),
      q = new THREE.Vector3(...b),
      d = q.clone().sub(p);
    const m = new THREE.Mesh(
      new THREE.BoxGeometry(width, d.length(), width),
      mat,
    );
    m.position.copy(p).add(q).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
    group.add(m);
  }
  // Use long mapped facade runs. Exact rooftop support counts remain unmeasured.
  for (let i = 0; i < points.length; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length],
      dx = b[0] - a[0],
      dz = b[1] - a[1],
      len = Math.hypot(dx, dz);
    if (len < 18) continue;
    const sign = area > 0 ? 1 : -1,
      ix = (-sign * dz) / len,
      iz = (sign * dx) / len;
    const n = Math.max(3, Math.floor(len / 4));
    for (let j = 1; j < n; j++) {
      const x = a[0] + (dx * j) / n,
        z = a[1] + (dz * j) / n;
      const p = [x + ix * 1.2, height, z + iz * 1.2],
        q = [x + ix * 4.2, height, z + iz * 4.2];
      beam(p, [p[0], height + 3.6, p[2]]);
      beam(q, [q[0], height + 3.6, q[2]]);
      beam([p[0], height + 3.6, p[2]], [q[0], height + 3.6, q[2]]);
    }
  }
  return group;
}
