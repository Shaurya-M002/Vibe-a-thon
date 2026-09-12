import { installInteriorView } from "./interior-view.js?v=21";
import { facadeLayer, roofFrames } from "./video-model.js?v=21";
import { createJourneyEvidence } from "./journey.js?v=21";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { footprint, flat } from "./geometry.js";
import {
  pathSurface,
  polylineLength,
  internalRoads,
  heightEstimate,
  clearanceWidth,
} from "./paths.js?v=21";
import { OBJExporter } from "three/addons/exporters/OBJExporter.js";
const $ = (id) => document.getElementById(id),
  data = await loadJSON("./model-data.json");
const params = new URLSearchParams(location.search),
  collection = "all";
let source = null;
let journeyPanel = null;
$("collection").value = collection;
$("collection").onchange = () => {
  location.href = "?dataset=" + $("collection").value;
};
{
  document.querySelector(".finding").hidden = true;
  source = await loadJSON("/api/recordings");
  if (!source.sessions?.length) {
    $("loading").textContent =
      "No recordings. Run python demo.py or provide a playback database.";
    throw new Error("No playback sessions");
  }
  data.sessions = {};
  for (const r of source.sessions)
    data.sessions[r.id] = {
      duration: r.duration,
      steps: r.steps,
      height: r.height,
      stepPath: [],
      events: r.events["2"],
      gps: r.gps.map((p) => [
        p[0],
        (p[2] - data.origin[1]) *
          111320 *
          Math.cos((data.origin[0] * Math.PI) / 180),
        -(p[1] - data.origin[0]) * 111320,
        p[3],
      ]),
    };
  $("session").replaceChildren(
    ...source.sessions.map(
      (r) => new Option(`Session ${r.id} · ${r.start}`, r.id),
    ),
  );
  document.querySelector(".legend span:nth-child(2)").hidden = true;
  $("route").innerHTML =
    '<option value="gps">Recorded GPS</option><option value="none">Buildings only</option>';
}
const renderer = new THREE.WebGLRenderer({
  canvas: $("scene"),
  antialias: true,
  alpha: false,
});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setClearColor(0x17262c);
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x17262c, 800, 1700);
const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 2500);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2 - 0.015;
controls.minDistance = 25;
controls.maxDistance = 1800;
scene.add(new THREE.HemisphereLight(0xdff8ff, 0x314839, 2.2));
const sun = new THREE.DirectionalLight(0xffe7bc, 3);
sun.position.set(-180, 350, 130);
scene.add(sun);
const model = new THREE.Group();
scene.add(model);
const surroundings = new THREE.Group();
scene.add(surroundings);
const phase2 = new THREE.Group();
scene.add(phase2);
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(850, 780),
  new THREE.MeshStandardMaterial({ color: 0x203639, roughness: 1 }),
);
ground.rotation.x = -Math.PI / 2;
ground.position.set(-120, -0.7, -60);
scene.add(ground);
const grid = new THREE.GridHelper(800, 40, 0x426064, 0x2c4649);
grid.position.set(-120, -0.55, -60);
scene.add(grid);
function line(points, color, width = 1, group = scene) {
  const g = new THREE.BufferGeometry().setFromPoints(
    points.map((p) => new THREE.Vector3(...p)),
  );
  const m = new THREE.Line(
    g,
    new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.7,
      linewidth: width,
    }),
  );
  group.add(m);
  return m;
}
const facadeGroups = [],
  roofGroups = [];
const mainMeshes = [],
  floorGroups = [];
const roadGroup = new THREE.Group();
scene.add(roadGroup);
for (const b of data.buildings) {
  const h = b.main ? 60 : 8;
  const g = footprint(b.points, h);
  const mat = new THREE.MeshStandardMaterial({
    color: b.main ? 0xdadbd3 : 0x566461,
    roughness: 0.86,
    metalness: 0.06,
    transparent: b.main,
    opacity: 1,
    depthWrite: true,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(g, mat);
  mesh.userData = b;
  const group = b.id === 146873096 ? phase2 : b.main ? model : surroundings;
  group.add(mesh);
  if (b.main) {
    mainMeshes.push(mesh);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(g, 25),
      new THREE.LineBasicMaterial({
        color: 0xc0e1d7,
        transparent: true,
        opacity: 0.37,
      }),
    );
    group.add(edges);
    mesh.userData.edges = edges;
    const floors = new THREE.Group();
    for (let j = 1; j < 20; j++)
      line(
        [...b.points, b.points[0]].map((p) => [p[0], j * 3, p[1]]),
        0x8db5b0,
        1,
        floors,
      );
    group.add(floors);
    floorGroups.push(floors);
  }
}
for (const f of data.context) {
  const t = f.tags;
  let color = null;
  if (t.landuse === "residential" && t.name?.includes("Elita"))
    color = 0x385346;
  else if (t.leisure === "park" || t.landuse === "grass") color = 0x3d634f;
  else if (t.leisure === "swimming_pool") color = 0x489daf;
  else if (t.leisure === "pitch") color = 0x47786c;
  const western =
    f.points.reduce((s, p) => s + p[0], 0) / f.points.length < -145;
  const group = western ? phase2 : surroundings;
  if (color) {
    const m = new THREE.Mesh(
      flat(f.points),
      new THREE.MeshStandardMaterial({
        color,
        roughness: 0.94,
        side: THREE.DoubleSide,
      }),
    );
    m.position.y = t.landuse === "residential" ? -0.35 : 0.06;
    group.add(m);
  }
}
// A ground compass and physical scale remain fixed in the geographic coordinate system.
scene.add(
  new THREE.ArrowHelper(
    new THREE.Vector3(0, 0, -1),
    new THREE.Vector3(115, 1, 125),
    25,
    0xd2e5db,
    6,
    4,
  ),
);
line(
  [
    [65, 1, 145],
    [115, 1, 145],
  ],
  0xc2ddd5,
);
line(
  [
    [65, 1, 142],
    [65, 1, 148],
  ],
  0xc2ddd5,
);
line(
  [
    [115, 1, 142],
    [115, 1, 148],
  ],
  0xc2ddd5,
);
let labelItems = [];
function label(text, pos, cls = "") {
  const el = document.createElement("div");
  el.className = "pin " + cls;
  el.textContent = text;
  $("labels").append(el);
  labelItems.push({ el, pos: new THREE.Vector3(...pos) });
  return el;
}
const fixedLabels = [["A5 · Maps place marker", [...data.a5], "a5"]];
let routeGroup = new THREE.Group();
scene.add(routeGroup);
let sid = data.sessions[params.get("session")]
  ? params.get("session")
  : Object.keys(data.sessions).at(-1);
let t = Math.max(
  0,
  Math.min(Number(params.get("time")) || 0, data.sessions[sid].duration),
);
let playing = false,
  last = 0;
$("session").value = sid;
$("time").max = data.sessions[sid].duration;
function sample(a, time) {
  let lo = 0,
    hi = a.length;
  while (lo < hi) {
    let m = (lo + hi) >> 1;
    if (a[m][0] <= time) lo = m + 1;
    else hi = m;
  }
  return lo ? a[lo - 1] : null;
}
function heightAt(time) {
  return sample(data.sessions[sid].height, time)?.[1] ?? 0;
}
function z(p) {
  return $("elevated").checked ? heightAt(p[0]) + 2 : 1.6;
}
function tube(a, b, color, r = 0.48) {
  const va = new THREE.Vector3(...a),
    vb = new THREE.Vector3(...b),
    d = vb.clone().sub(va);
  if (d.length() < 0.005) return;
  const m = new THREE.Mesh(
    new THREE.CylinderGeometry(r, r, d.length(), 6),
    new THREE.MeshBasicMaterial({
      color,
      depthTest: false,
      transparent: true,
      opacity: 0.95,
    }),
  );
  m.position.copy(va).add(vb).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
  m.renderOrder = 5;
  routeGroup.add(m);
}
function marker(p, color, text) {
  const y = z(p);
  tube([p[1], y, p[2]], [p[1], y + 9, p[2]], color, 0.35);
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(1.7, 12, 10),
    new THREE.MeshBasicMaterial({ color, depthTest: false }),
  );
  mesh.position.set(p[1], y + 9, p[2]);
  mesh.renderOrder = 8;
  routeGroup.add(mesh);
  label(text, [p[1], y + 12, p[2]]);
}
function rebuild() {
  scene.remove(routeGroup);
  routeGroup.traverse((o) => {
    o.geometry?.dispose();
    o.material?.dispose();
  });
  routeGroup = new THREE.Group();
  scene.add(routeGroup);
  $("labels").replaceChildren();
  labelItems = [];
  label(
    "A5 · Maps marker",
    [
      data.a5[0],
      heightEstimate(+$("levels").value, +$("storey").value) + 3,
      data.a5[1],
    ],
    "a5",
  );
  label("N", [115, 2, 95]);
  label("50 m", [90, 1, 154]);
  const s = data.sessions[sid],
    mode = $("route").value;
  if (mode !== "none") {
    for (const kind of ["gps", "steps"]) {
      if (mode !== kind && mode !== "both") continue;
      const points = kind === "gps" ? s.gps : s.stepPath;
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1],
          b = points[i];
        if (kind === "gps" && b[0] - a[0] > 4) continue;
        const isClimb = s.events.some((e) => b[0] >= e.start && b[0] <= e.end);
        const color =
          isClimb && $("elevated").checked
            ? 0xb883ee
            : kind === "gps"
              ? 0x2485ff
              : 0xed9e39;
        tube(
          [a[1], z(a), a[2]],
          [b[1], z(b), b[2]],
          color,
          kind === "gps" ? 0.42 : 0.5,
        );
      }
      const now = sample(points, t);
      if (now && (kind !== "gps" || t - now[0] <= 4)) {
        const mesh = new THREE.Mesh(
          new THREE.SphereGeometry(2.2, 16, 12),
          new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false }),
        );
        mesh.position.set(now[1], z(now), now[2]);
        mesh.renderOrder = 10;
        routeGroup.add(mesh);
      }
    }
    if (s.gps.length) {
      marker(s.gps[0], 0x56d3b0, `GPS start · ±${s.gps[0][3].toFixed(1)} m`);
      marker(
        s.gps.at(-1),
        0xef8d80,
        `GPS end · ±${s.gps.at(-1)[3].toFixed(1)} m`,
      );
      const p = s.gps[0];
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(p[3] - 0.35, p[3] + 0.35, 64),
        new THREE.MeshBasicMaterial({
          color: 0x56d3b0,
          side: THREE.DoubleSide,
          transparent: true,
          opacity: 0.5,
          depthTest: false,
        }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(p[1], 1, p[2]);
      ring.renderOrder = 6;
      routeGroup.add(ring);
    }
  }
  $("stepCount").textContent = s.steps.filter(
    (p) => (Array.isArray(p) ? p[0] : p) <= t,
  ).length;
  $("rise").textContent =
    (heightAt(t) >= 0 ? "+" : "") + heightAt(t).toFixed(1) + " m";
  $("clock").textContent =
    Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
  $("time").value = t;
  journeyPanel?.update(Number(sid), t);
  {
    $("story").textContent =
      source.sessions.find((r) => String(r.id) === sid)?.story ||
      (s.events.length
        ? s.events
            .map(
              (e) =>
                `${e.start}–${e.end}s: ${e.kind}, ${e.delta > 0 ? "+" : ""}${e.delta.toFixed(1)}m, ${e.steps} steps.`,
            )
            .join(" ")
        : `No vertical candidate passes the ${$("eventThreshold").value}m threshold.`);
    if (!s.gps.length)
      $("story").textContent += " No valid GPS: no route can be placed.";
    $("climb").disabled = !s.events.length;
    $("climb").textContent = "Inspect height candidate ↗";
    $("routeNote").textContent =
      "GPS route drawn over buildings for visibility, not constrained to corridors. Pins mark the first and last GPS fixes. " +
      ($("elevated").checked
        ? "Pressure height is relative to this recording’s start, placed near ground level for display. Its actual floor is unknown."
        : "Route displayed near ground level; its actual floor is unknown.");
  }
}

function home(top = false, focus = false) {
  const whole = $("whole").checked;
  const target = focus
    ? new THREE.Vector3(data.a5[0], 10, data.a5[1])
    : whole
      ? new THREE.Vector3(-135, 0, -85)
      : new THREE.Vector3(0, 8, -4);
  controls.target.copy(target);
  camera.position
    .copy(target)
    .add(
      top
        ? new THREE.Vector3(0, whole ? 720 : 440, 0.1)
        : focus
          ? new THREE.Vector3(95, 100, 130)
          : whole
            ? new THREE.Vector3(390, 400, 470)
            : new THREE.Vector3(230, 235, 310),
    );
  if (whole && !focus) {
    const box = new THREE.Box3().setFromPoints(
      mainMeshes.flatMap((m) =>
        m.userData.points.flatMap((p) => [
          new THREE.Vector3(p[0], 0, p[1]),
          new THREE.Vector3(p[0], 65, p[1]),
        ]),
      ),
    );
    const center = box.getCenter(new THREE.Vector3()),
      radius = box.getSize(new THREE.Vector3()).length() / 2 + 20;
    controls.target.copy(center);
    camera.position
      .copy(center)
      .add(
        (top ? new THREE.Vector3(0, 1, 0.001) : new THREE.Vector3(0.6, 0.85, 1))
          .normalize()
          .multiplyScalar(
            radius /
              Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) /
              Math.min(1, camera.aspect),
          ),
      );
  } else if (!focus) {
    const offset = camera.position.clone().sub(target);
    camera.position
      .copy(target)
      .add(offset.multiplyScalar(1 / Math.min(1, camera.aspect)));
  }
  controls.update();
  $("top").classList.toggle("active", top);
  $("oblique").classList.toggle("active", !top);
  $("viewTitle").textContent = focus
    ? "A5 and the recorded routes."
    : top
      ? "The shape of the place."
      : "A new perspective.";
}
function fitRoute(top = $("top").classList.contains("active")) {
  const points = data.sessions[sid].gps;
  if (!points.length) {
    home(top);
    return;
  }
  const box = new THREE.Box3().setFromPoints(
    points.map((p) => new THREE.Vector3(p[1], z(p), p[2])),
  );
  const center = box.getCenter(new THREE.Vector3()),
    size = box.getSize(new THREE.Vector3());
  const radius = Math.max(55, size.length() / 2 + 30);
  const distance =
    radius /
    Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) /
    Math.min(1, camera.aspect);
  controls.target.copy(center);
  camera.position
    .copy(center)
    .add(
      (top ? new THREE.Vector3(0, 1, 0.001) : new THREE.Vector3(0.6, 0.9, 1))
        .normalize()
        .multiplyScalar(distance),
    );
  controls.update();
  $("top").classList.toggle("active", top);
  $("oblique").classList.toggle("active", !top);
  $("viewTitle").textContent = top
    ? "Your route from above."
    : "Your route through Elita.";
}
$("fitRoute").onclick = () => fitRoute();
$("top").onclick = () => fitRoute(true);
$("oblique").onclick = () => fitRoute(false);
$("reset").onclick = () => {
  if (interiorView.active) interiorView.fit();
  else fitRoute(false);
};
$("focus").onclick = () => home(false, true);
$("whole").onchange = () => {
  phase2.visible = $("whole").checked;
  $("phaseLabel").textContent = $("whole").checked
    ? "ELITA PROMENADE / BOTH PHASES"
    : "ELITA PROMENADE / PHASE 1";
  updateHeight();
  updateRoads();
  home();
};
phase2.visible = true;
$("ghost").onchange = () => {
  for (const m of mainMeshes) {
    m.material.opacity = $("ghost").checked ? 0.43 : 1;
    m.material.depthWrite = !$("ghost").checked;
    m.material.needsUpdate = true;
  }
  setFacadeVisibility();
};
let selectedLevel = new THREE.Group();
scene.add(selectedLevel);
function clearGroup(g) {
  g.traverse((o) => {
    o.geometry?.dispose();
    o.material?.map?.dispose();
    o.material?.dispose();
  });
  g.clear();
}
function updateFacades() {
  for (const g of roofGroups) {
    g.removeFromParent();
    clearGroup(g);
  }
  roofGroups.length = 0;
  for (const m of mainMeshes) {
    const g = roofFrames(
      m.userData.points,
      heightEstimate(+$("levels").value, +$("storey").value),
    );
    m.parent.add(g);
    roofGroups.push(g);
  }
  for (const g of facadeGroups) {
    g.removeFromParent();
    clearGroup(g);
  }
  facadeGroups.length = 0;
  for (const m of mainMeshes) {
    const g = facadeLayer(
      m.userData.points,
      +$("levels").value,
      +$("storey").value,
    );
    m.parent.add(g);
    facadeGroups.push(g);
  }
  setFacadeVisibility();
}
function setFacadeVisibility() {
  for (const g of [...facadeGroups, ...roofGroups])
    g.visible = $("facades").checked && !$("ghost").checked;
}
$("facades").onchange = setFacadeVisibility;
function updateHeight() {
  const levels = +$("levels").value,
    storey = +$("storey").value,
    h = heightEstimate(levels, storey);
  $("heightValue").textContent = h.toFixed(1) + " m";
  $("storeyValue").textContent = storey.toFixed(2) + " m · assumed";
  $("heightRange").textContent =
    levels +
    " levels × " +
    storey.toFixed(2) +
    " m. Sensitivity: " +
    (levels * 2.8).toFixed(1) +
    "–" +
    (levels * 3.3).toFixed(1) +
    " m at 2.8–3.3 m per level; roof structures excluded.";
  $("slice").max = levels;
  if (+$("slice").value > levels) $("slice").value = levels;
  for (const m of mainMeshes) {
    m.scale.y = h / 60;
    m.userData.edges.scale.y = h / 60;
  }
  for (let i = 0; i < floorGroups.length; i++) {
    const g = floorGroups[i];
    clearGroup(g);
    const points = mainMeshes[i].userData.points;
    for (let j = 1; j < levels; j++)
      line(
        [...points, points[0]].map((p) => [p[0], j * storey, p[1]]),
        0x8db5b0,
        1,
        g,
      );
  }
  clearGroup(selectedLevel);
  const level = +$("slice").value;
  $("levelValue").textContent = level
    ? "Level boundary " + level + " · " + (level * storey).toFixed(1) + " m"
    : "Off";
  if (level)
    for (const m of mainMeshes) {
      if (m.userData.id === 146873096 && !$("whole").checked) continue;
      const slab = new THREE.Mesh(
        flat(m.userData.points),
        new THREE.MeshBasicMaterial({
          color: 0xf0b45d,
          transparent: true,
          opacity: 0.7,
          side: THREE.DoubleSide,
          depthTest: false,
        }),
      );
      slab.position.y = level * storey;
      slab.renderOrder = 12;
      selectedLevel.add(slab);
    }
  for (const l of labelItems)
    if (l.el.classList.contains("a5")) l.pos.y = h + 3;
  updateFacades();
}
for (const id of ["levels", "storey", "slice"]) $(id).oninput = updateHeight;
const community = internalRoads(data);
function updateRoads() {
  clearGroup(roadGroup);
  const width = +$("roadWidth").value;
  $("widthValue").textContent = width.toFixed(1) + " m · assumed";
  let shown = 0,
    total = 0;
  for (const f of community) {
    const west =
      f.points.reduce((s, p) => s + p[0], 0) / f.points.length < -145;
    if (west && !$("whole").checked) continue;
    const requested = f.tags.highway === "footway" ? 1.8 : width,
      w = clearanceWidth(
        f.points,
        requested,
        data.buildings.filter((b) => b.main).map((b) => b.points),
      );
    const mesh = new THREE.Mesh(
      pathSurface(f.points, w),
      new THREE.MeshStandardMaterial({
        color: 0xb7b6a4,
        roughness: 1,
        side: THREE.DoubleSide,
      }),
    );
    mesh.name = "OSM_path_" + f.id;
    roadGroup.add(mesh);
    shown++;
    total += polylineLength(f.points);
  }
  roadGroup.visible = $("showRoads").checked;
  $("pathSummary").textContent =
    shown +
    " mapped segments · " +
    Math.round(total) +
    " m of centre lines shown. Round joins retain every OSM bend and junction. Widths are assumed and reduced where necessary to avoid mapped buildings; this is not measured road width.";
}
$("roadWidth").oninput = updateRoads;
$("showRoads").onchange = () => {
  roadGroup.visible = $("showRoads").checked;
};
$("exportModel").onclick = () => {
  if (interiorView.active) {
    const text =
      "# User-corrected three-bedroom layout. Approximate dimensions. Not georeferenced.\n" +
      new OBJExporter().parse(interiorView.group);
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" })),
      a = document.createElement("a");
    a.href = url;
    a.download = "elita-apartment-layout.obj";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return;
  }
  const g = new THREE.Group();
  for (const m of mainMeshes) {
    const clone = m.clone();
    clone.name =
      m.userData.id === 350866746 ? "Elita_Phase_A" : "Elita_Phase_B";
    g.add(clone);
  }
  if (roadGroup.visible) g.add(roadGroup.clone());
  for (const roof of roofGroups) if (roof.visible) g.add(roof.clone());
  g.updateMatrixWorld(true);
  const text =
    "# Approximate Elita model. OSM contributors, ODbL. Metres Y-up. Floor height assumed.\n" +
    new OBJExporter().parse(g);
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" })),
    a = document.createElement("a");
  a.href = url;
  a.download = "elita-current-model.obj";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
};
$("session").onchange = () => {
  sid = $("session").value;
  history.replaceState(null, "", `?dataset=${collection}&session=${sid}`);
  t = 0;
  playing = false;
  $("play").textContent = "▶";
  $("time").max = data.sessions[sid].duration;
  rebuild();
  fitRoute();
};
$("route").onchange = rebuild;
$("elevated").onchange = rebuild;
$("time").oninput = () => {
  t = +$("time").value;
  rebuild();
};
$("play").onclick = () => {
  playing = !playing;
  if (t >= data.sessions[sid].duration) t = 0;
  $("play").textContent = playing ? "Ⅱ" : "▶";
};
$("climb").onclick = () => {
  t = data.sessions[sid].events[0]?.start ?? 0;
  $("elevated").checked = true;
  rebuild();
  const end = data.sessions[sid].events[0]?.end ?? t + 12;
  focusInterval(t, end);
};
journeyPanel = createJourneyEvidence(source, collection, (id, time) => {
  sid = String(id);
  $("session").value = sid;
  t = time;
  playing = false;
  $("play").textContent = "▶";
  $("time").max = data.sessions[sid].duration;
  history.replaceState(null, "", `?dataset=${collection}&session=${sid}`);
  rebuild();
});
$("eventThreshold").addEventListener("change", () => {
  {
    for (const r of source.sessions)
      data.sessions[r.id].events = r.events[$("eventThreshold").value];
    rebuild();
  }
});
const resize = new ResizeObserver(() => {
  const w = $("stage").clientWidth,
    h = $("stage").clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
});
resize.observe($("stage"));
camera.aspect = $("stage").clientWidth / $("stage").clientHeight;
camera.updateProjectionMatrix();
fitRoute(false);
rebuild();
updateHeight();
updateRoads();
$("loading").remove();
$("ghost").checked = false;
$("ghost").onchange();
function animate(now) {
  requestAnimationFrame(animate);
  if (playing && now - last > 100) {
    t = Math.min(
      data.sessions[sid].duration,
      t + ((now - last) / 1000) * Number($("playSpeed").value),
    );
    if (t >= data.sessions[sid].duration) {
      playing = false;
      $("play").textContent = "▶";
    }
    rebuild();
    last = now;
  }
  if (!playing) last = now;
  controls.update();
  renderer.render(scene, camera);
  for (const l of labelItems) {
    const p = l.pos.clone().project(camera);
    l.el.style.left = (p.x * 0.5 + 0.5) * $("stage").clientWidth + "px";
    l.el.style.top = (-p.y * 0.5 + 0.5) * $("stage").clientHeight + "px";
    l.el.style.display =
      Math.abs(p.x) < 0.96 && Math.abs(p.y) < 0.92 && p.z < 1
        ? "block"
        : "none";
  }
}
requestAnimationFrame(animate);

// Georeferenced tile planes share the model's east / north coordinate transform.
const basemapGroup = new THREE.Group();
scene.add(basemapGroup);
let mapRevision = 0;
const tileLoader = new THREE.TextureLoader();
tileLoader.setCrossOrigin("anonymous");
function tileLat(y, n) {
  return (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
}
function mapXY(lat, lon) {
  return [
    (lon - data.origin[1]) *
      111320 *
      Math.cos((data.origin[0] * Math.PI) / 180),
    -(lat - data.origin[0]) * 111320,
  ];
}
function updateBasemap() {
  const revision = ++mapRevision;
  for (const child of [...basemapGroup.children]) {
    child.geometry.dispose();
    child.material.map?.dispose();
    child.material.dispose();
    basemapGroup.remove(child);
  }
  const mode = $("groundMap").value;
  grid.visible = mode === "grid";
  $("groundCredit").innerHTML =
    mode === "osm"
      ? '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>'
      : mode === "satellite"
        ? "Tiles © Esri · Maxar, Earthstar Geographics and the GIS User Community"
        : "Local ground grid · no map requests";
  if (mode === "grid") return;
  const z = 18,
    n = 2 ** z,
    tx = ((data.origin[1] + 180) / 360) * n,
    ty =
      ((1 -
        Math.log(Math.tan(Math.PI / 4 + (data.origin[0] * Math.PI) / 360)) /
          Math.PI) /
        2) *
      n;
  for (let x = Math.floor(tx) - 4; x <= Math.floor(tx) + 2; x++)
    for (let y = Math.floor(ty) - 3; y <= Math.floor(ty) + 2; y++) {
      const nw = mapXY(tileLat(y, n), (x / n) * 360 - 180),
        se = mapXY(tileLat(y + 1, n), ((x + 1) / n) * 360 - 180);
      const url =
        mode === "osm"
          ? `https://tile.openstreetmap.org/${z}/${x}/${y}.png`
          : `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
      tileLoader.load(
        url,
        (texture) => {
          if (revision !== mapRevision) {
            texture.dispose();
            return;
          }
          texture.colorSpace = THREE.SRGBColorSpace;
          texture.anisotropy = Math.min(
            8,
            renderer.capabilities.getMaxAnisotropy(),
          );
          const tile = new THREE.Mesh(
            new THREE.PlaneGeometry(se[0] - nw[0], se[1] - nw[1]),
            new THREE.MeshBasicMaterial({ map: texture }),
          );
          tile.rotation.x = -Math.PI / 2;
          tile.position.set((nw[0] + se[0]) / 2, -0.2, (nw[1] + se[1]) / 2);
          basemapGroup.add(tile);
        },
        undefined,
        () => {
          if (
            revision === mapRevision &&
            !$("groundCredit").textContent.includes("unavailable")
          )
            $("groundCredit").append(" · Some tiles unavailable");
        },
      );
    }
}
$("groundMap").onchange = updateBasemap;
updateBasemap();

function focusInterval(start, end) {
  const points = data.sessions[sid].gps.filter(
    (p) => p[0] >= start && p[0] <= end,
  );
  if (!points.length) {
    $("story").textContent +=
      " No GPS within this interval; map view retained.";
    return;
  }
  const box = new THREE.Box3().setFromPoints(
      points.map((p) => new THREE.Vector3(p[1], z(p), p[2])),
    ),
    center = box.getCenter(new THREE.Vector3()),
    radius = Math.max(25, box.getSize(new THREE.Vector3()).length() / 2 + 15);
  controls.target.copy(center);
  camera.position
    .copy(center)
    .add(
      new THREE.Vector3(0.6, 0.9, 1)
        .normalize()
        .multiplyScalar(
          radius /
            Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) /
            Math.min(1, camera.aspect),
        ),
    );
  controls.update();
  $("viewTitle").textContent = "GPS during the height event.";
}

const interiorView = installInteriorView({
  scene,
  camera,
  controls,
  stopPlayback() {
    playing = false;
    $("play").textContent = "▶";
  },
  home() {
    rebuild();
    fitRoute(false);
  },
});

$("stageFit").onclick = () => {
  if (interiorView.active) interiorView.fit();
  else fitRoute();
};

const fullButton = $("stageFullscreen");
fullButton.onclick = async () => {
  if (document.fullscreenElement) {
    await document.exitFullscreen();
    return;
  }
  if ($("stage").requestFullscreen) {
    try {
      await $("stage").requestFullscreen();
      return;
    } catch {}
  }
  document.body.classList.toggle("map-expanded");
  fullButton.textContent = document.body.classList.contains("map-expanded")
    ? "Exit fullscreen"
    : "Fullscreen";
};
document.addEventListener("fullscreenchange", () => {
  fullButton.textContent = document.fullscreenElement
    ? "Exit fullscreen"
    : "Fullscreen";
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.body.classList.remove("map-expanded");
    if (!document.fullscreenElement) fullButton.textContent = "Fullscreen";
  }
});
async function loadJSON(url) {
  try {
    const response = await fetch(url);
    const payload = await response.json();
    if (!response.ok)
      throw new Error(payload.error || `Request failed: ${response.status}`);
    return payload;
  } catch (error) {
    document.getElementById("loading").textContent =
      `Unable to load the portal: ${error.message}`;
    throw error;
  }
}
