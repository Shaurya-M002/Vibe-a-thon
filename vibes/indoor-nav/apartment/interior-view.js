import * as THREE from "three";
import { buildInterior, unitPlan, planPoint } from "./interior-plan.js?v=21";
// Owns only an unregistered interior view; cannot mutate the geographical model.
export function installInteriorView({
  scene,
  camera,
  controls,
  stopPlayback,
  home,
}) {
  const $ = (id) => document.getElementById(id);
  let active = false,
    saved = [],
    hiddenUI = [],
    interior = null,
    cutaway = true,
    lastTop = false,
    furnished = false;
  function dispose() {
    if (!interior) return;
    interior.traverse((o) => {
      o.geometry?.dispose();
      o.material?.map?.dispose();
      o.material?.dispose();
    });
    interior.removeFromParent();
    interior = null;
  }
  function frame(top = false) {
    lastTop = top;
    $("interiorTop").classList.toggle("active", top);
    $("interiorOrbit").classList.toggle("active", !top);
    camera.aspect = $("stage").clientWidth / $("stage").clientHeight;
    camera.updateProjectionMatrix();
    const box = new THREE.Box3().setFromObject(interior),
      center = box.getCenter(new THREE.Vector3()),
      direction = (
        top ? new THREE.Vector3(0, 1, 0.001) : new THREE.Vector3(0.55, 0.8, 1)
      ).normalize(),
      right = new THREE.Vector3()
        .crossVectors(new THREE.Vector3(0, 1, 0), direction)
        .normalize(),
      up = new THREE.Vector3().crossVectors(direction, right);
    let distance = 0;
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    for (const x of [box.min.x, box.max.x])
      for (const y of [box.min.y, box.max.y])
        for (const z of [box.min.z, box.max.z]) {
          const p = new THREE.Vector3(x, y, z).sub(center);
          distance = Math.max(
            distance,
            p.dot(direction) + Math.abs(p.dot(right)) / (tan * camera.aspect),
            p.dot(direction) + Math.abs(p.dot(up)) / tan,
          );
        }
    controls.target.copy(center);
    camera.position.copy(center).add(direction.multiplyScalar(distance * 1.17));
    controls.update();
  }

  function render() {
    dispose();
    interior = buildInterior(cutaway, furnished);
    scene.add(interior);
    $("interiorWallMode").textContent = cutaway
      ? "Full-height walls"
      : "Cutaway walls";
    $("interiorFurnish").textContent = furnished
      ? "Hide furniture"
      : "Show furniture";
  }
  function open() {
    if (active) return;
    stopPlayback();
    active = true;
    $("exportHelp").textContent = "Apartment model · OBJ format.";
    document.body.classList.add("interior-mode");
    document.querySelector("aside>h1").textContent = "Your apartment.";
    hiddenUI = [
      $("journeyOverview"),
      $("journeyEvidence"),
      document.querySelector(".legend"),
      ...document.querySelectorAll("aside>section"),
      document.querySelector("aside>.eyebrow"),
      document.querySelector(".below"),
    ].map((el) => [el, el.hidden]);
    for (const [el] of hiddenUI) el.hidden = true;
    saved = scene.children.filter((o) => !o.isLight).map((o) => [o, o.visible]);
    for (const [o] of saved) o.visible = false;
    render();
    controls.minDistance = 1.5;
    controls.maxDistance = 90;
    $("labels").hidden = true;
    document.querySelector("aside").append($("interiorToolbar"));
    $("interiorToolbar").hidden = false;
    $("phaseLabel").hidden = true;
    $("viewTitle").textContent = "Inside the apartment.";
    $("routeNote").textContent =
      "Three bedrooms · master en-suite · utility beside the kitchen";
    frame(true);
    $("stage").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function close() {
    if (!active) return;
    active = false;
    dispose();
    document.body.classList.remove("interior-mode");
    document.querySelector("aside>h1").textContent = "Your journey.";
    $("exportHelp").textContent = "Buildings and paths · OBJ format.";
    for (const [el, hidden] of hiddenUI) el.hidden = hidden;
    hiddenUI = [];
    for (const [o, visible] of saved) o.visible = visible;
    saved = [];
    controls.minDistance = 25;
    controls.maxDistance = 1800;
    $("labels").hidden = false;
    $("interiorToolbar").hidden = true;
    $("phaseLabel").hidden = false;
    $("phaseLabel").textContent = "ELITA PROMENADE";
    camera.aspect = $("stage").clientWidth / $("stage").clientHeight;
    camera.updateProjectionMatrix();
    home();
  }
  $("interiorFurnish").onclick = () => {
    furnished = !furnished;
    render();
  };
  $("interiorDownload").onclick = () => $("exportModel").click();
  $("interiorPlan").onclick = open;
  $("interiorBack").onclick = close;
  $("interiorTop").onclick = () => {
    cutaway = true;
    render();
    frame(true);
  };
  $("interiorOrbit").onclick = () => {
    cutaway = true;
    render();
    frame(false);
  };
  $("interiorWallMode").onclick = () => {
    cutaway = !cutaway;
    render();
  };
  $("interiorEntry").onclick = () => {
    cutaway = false;
    render();
    const a = planPoint([842, 614]),
      b = planPoint([825, 320]);
    camera.position.set(a[0], 1.6, a[1]);
    controls.target.set(b[0], 1.6, b[1]);
    controls.update();
  };
  // Any recording/map change returns to geographic coordinates before it runs.
  for (const [id, event] of [
    ["top", "click"],
    ["oblique", "click"],
    ["fitRoute", "click"],
    ["focus", "click"],
    ["climb", "click"],
    ["play", "click"],
    ["session", "change"],
    ["collection", "change"],
    ["time", "input"],
    ["route", "change"],
    ["elevated", "change"],
    ["groundMap", "change"],
    ["levels", "input"],
    ["storey", "input"],
    ["slice", "input"],
    ["facades", "change"],
    ["ghost", "change"],
    ["roadWidth", "input"],
    ["showRoads", "change"],
  ])
    $(id).addEventListener(event, close, true);
  $("journeyEvidence").addEventListener("click", close, true);
  return {
    get active() {
      return active;
    },
    get group() {
      return interior;
    },
    fit() {
      frame(lastTop);
    },
    close,
  };
}
