import * as THREE from "three";
// Schematic furniture communicates scale and room use; it is not an inventory.
export function furnishInterior(planPoint, pixelsPerMetre) {
  const group = new THREE.Group();
  group.name = "Schematic furniture";
  const palette = new Map();
  const material = (color) => {
    if (!palette.has(color))
      palette.set(
        color,
        new THREE.MeshStandardMaterial({ color, roughness: 0.85 }),
      );
    return palette.get(color);
  };
  function box(x, z, w, d, h, color, y = 0) {
    const p = planPoint([x, z]),
      m = new THREE.Mesh(
        new THREE.BoxGeometry(w / pixelsPerMetre, h, d / pixelsPerMetre),
        material(color),
      );
    m.position.set(p[0], y + h / 2, p[1]);
    group.add(m);
    return m;
  }
  for (const [x, z] of [
    [140, 200],
    [375, 195],
    [115, 445],
  ]) {
    box(x, z, 90, 120, 0.24, 0x8b806e);
    box(x, z, 86, 116, 0.2, 0xf1eee3, 0.24);
    box(x, z + 15, 84, 82, 0.055, 0x93aaa2, 0.44);
    box(x, z - 40, 70, 20, 0.1, 0xfaf7ee, 0.44);
  }
  box(785, 165, 150, 55, 0.4, 0x9eaea7);
  box(785, 140, 150, 10, 0.7, 0x9eaea7);
  box(775, 235, 75, 38, 0.35, 0xa78f6e);
  box(570, 190, 85, 55, 0.74, 0xb6a38a);
  for (const x of [545, 595])
    for (const z of [145, 235]) box(x, z, 24, 25, 0.42, 0x82928a);
  box(765, 515, 30, 200, 0.86, 0xdbd7cc);
  box(692, 611, 175, 30, 0.86, 0xdbd7cc);
  box(765, 480, 27, 35, 0.03, 0x384747, 0.86);
  box(690, 611, 38, 22, 0.03, 0x809797, 0.86);
  box(555, 605, 40, 40, 0.85, 0xe3e6df);
  for (const x of [270, 475]) {
    box(x, 602, 28, 36, 0.42, 0xf0efe8);
    box(x, 582, 30, 15, 0.7, 0xf0efe8);
    box(x, 395, 40, 25, 0.8, 0xcac8bd);
  }
  box(350, 595, 48, 48, 0.035, 0xa8c4c6);
  return group;
}
