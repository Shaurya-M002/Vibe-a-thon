import fs from "node:fs";
import * as THREE from "three";
import { OBJExporter } from "three/addons/exporters/OBJExporter.js";
import { footprint } from "./geometry.js";
const d = JSON.parse(
    fs.readFileSync(new URL("./model-data.json", import.meta.url)),
  ),
  group = new THREE.Group();
for (const b of d.buildings.filter((b) => b.main)) {
  const m = new THREE.Mesh(
    footprint(b.points, 60),
    new THREE.MeshStandardMaterial(),
  );
  m.name =
    b.id === 350866746
      ? "Elita_Phase_1_OSM_350866746"
      : "Elita_Phase_2_OSM_146873096";
  group.add(m);
}
fs.writeFileSync(
  new URL("./elita-promenade.obj", import.meta.url),
  "# Elita Promenade approximate massing. Metres, Y up. 60 m assumed height.\n# OSM contributors, ODbL. Origin 12.8935 N, 77.5795 E. +X east, -Z north.\n" +
    new OBJExporter().parse(group),
);
console.log("Exported OBJ");
