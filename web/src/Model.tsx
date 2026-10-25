import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { Room } from "./api";

function twoLines(text: string) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = ["", ""];
  let line = 0;
  for (const word of words) {
    const next = (lines[line] + " " + word).trim();
    if (next.length <= 32) lines[line] = next;
    else if (line === 0) {
      line = 1;
      lines[1] = word;
    } else break;
  }
  return lines[1] ? `${lines[0]}\n${lines[1]}` : lines[0];
}

function separateLabels(root: HTMLElement) {
  const buttons = [...root.querySelectorAll<HTMLButtonElement>(".marker-label")];
  if (buttons.length < 2) return;
  const extra = buttons.map(() => 0);
  for (let pass = 0; pass < 4; pass += 1) {
    buttons.forEach((button, i) => {
      const base = button.dataset.base || "0px";
      button.style.transform = `translateX(calc(${base} + ${extra[i]}px))`;
    });
    void root.offsetWidth;
    const rects = buttons.map((button) => button.getBoundingClientRect());
    let hit = false;
    for (let i = 0; i < buttons.length; i += 1) {
      for (let j = i + 1; j < buttons.length; j += 1) {
        const a = rects[i];
        const b = rects[j];
        const overlapX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const overlapY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (overlapX <= 2 || overlapY <= 2) continue;
        hit = true;
        const dir = a.left + a.width / 2 <= b.left + b.width / 2 ? -1 : 1;
        const push = overlapX / 2 + 8;
        extra[i] += dir * push;
        extra[j] -= dir * push;
      }
    }
    if (!hit) return;
  }
  buttons.forEach((button, i) => {
    const base = button.dataset.base || "0px";
    button.style.transform = `translateX(calc(${base} + ${extra[i]}px))`;
  });
}

function ring(polygon: number[][]) {
  const pts = polygon.map((p) => [p[0], p[1]] as [number, number]);
  const first = pts[0];
  const last = pts[pts.length - 1];
  if (first && last && first[0] === last[0] && first[1] === last[1]) pts.pop();
  return pts;
}

function doorEdge(pts: [number, number][], door: { wall?: string; edge?: number }) {
  if (typeof door.edge === "number") return door.edge;
  let edge = 0;
  let best = door.wall === "north" ? -Infinity : Infinity;
  pts.forEach((p, i) => {
    const q = pts[(i + 1) % pts.length];
    const z = (p[1] + q[1]) / 2;
    if (door.wall === "north" ? z > best : z < best) {
      best = z;
      edge = i;
    }
  });
  return edge;
}

function addSegment(
  scene: { add: (mesh: object) => void },
  material: object,
  x0: number, z0: number, x1: number, z1: number,
  height: number, from: number, to: number,
) {
  const dx = x1 - x0;
  const dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  const seg = to - from;
  if (seg < 0.05 || len < 0.05) return;
  const t = (from + to) / 2 / len;
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(seg, height, 0.08), material);
  mesh.position.set(x0 + dx * t, height / 2, z0 + dz * t);
  mesh.rotation.y = -Math.atan2(dz, dx);
  scene.add(mesh);
}

function addFloor(scene: { add: (mesh: object) => void }, pts: [number, number][], y: number, material: object) {
  const shape = new THREE.Shape();
  shape.moveTo(pts[0][0], pts[0][1]);
  pts.slice(1).forEach((p) => shape.lineTo(p[0], p[1]));
  shape.closePath();
  const geo = new THREE.ShapeGeometry(shape);
  geo.rotateX(Math.PI / 2);
  const mesh = new THREE.Mesh(geo, material);
  mesh.position.y = y;
  scene.add(mesh);
}

function kindOf(name: string) {
  const n = name.toLowerCase();
  if (/hall|corridor/.test(n)) return "hallway";
  if (/bath|toilet|restroom|\bwc\b/.test(n)) return "bath";
  if (/kitchen|cook/.test(n)) return "kitchen";
  if (/din/.test(n)) return "dining";
  if (/liv|lounge|family/.test(n)) return "living";
  if (/bed|primary|master/.test(n)) return "bedroom";
  if (/closet|wardrobe|pantry/.test(n)) return "closet";
  return "room";
}

function bounds(pts: [number, number][]) {
  const xs = pts.map((p) => p[0]);
  const zs = pts.map((p) => p[1]);
  return { x0: Math.min(...xs), x1: Math.max(...xs), z0: Math.min(...zs), z1: Math.max(...zs) };
}

function mat(color: string, extras: Record<string, number | boolean | string> = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.72, ...extras });
}

function box(
  scene: { add: (mesh: object) => void },
  material: object,
  x: number, y: number, z: number,
  w: number, h: number, d: number,
) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.position.set(x, y, z);
  scene.add(mesh);
}

function cyl(
  scene: { add: (mesh: object) => void },
  material: object,
  x: number, y: number, z: number,
  r: number, h: number,
  axis: "y" | "x" | "z" = "y",
) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 14), material);
  mesh.position.set(x, y, z);
  if (axis === "x") mesh.rotation.z = Math.PI / 2;
  if (axis === "z") mesh.rotation.x = Math.PI / 2;
  scene.add(mesh);
}

function chair(scene: { add: (mesh: object) => void }, wood: object, cloth: object, x: number, z: number, yaw: number) {
  const group = new THREE.Group();
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.05, 0.38), cloth);
  seat.position.set(0, 0.42, 0);
  const back = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.4, 0.05), cloth);
  back.position.set(0, 0.64, -0.16);
  group.add(seat, back);
  for (const [lx, lz] of [[-0.14, -0.14], [0.14, -0.14], [-0.14, 0.14], [0.14, 0.14]]) {
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.4, 8), wood);
    leg.position.set(lx, 0.2, lz);
    group.add(leg);
  }
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  scene.add(group);
}

function furnish(scene: { add: (mesh: object) => void }, kind: string, x0: number, z0: number, x1: number, z1: number) {
  const cx = (x0 + x1) / 2;
  const cz = (z0 + z1) / 2;
  const wood = mat("#8a6f52");
  const dark = mat("#4a3f36");
  const linen = mat("#e4d8c8");
  const pillow = mat("#f3ece3");
  const cloth = mat("#6a7d8c");
  const seat = mat("#5b6e7c");
  const stone = mat("#d5d1c8");
  const top = mat("#ece6dc");
  const porcelain = mat("#f4f1ea");
  const water = mat("#8aa4b3", { transparent: true, opacity: 0.45 });
  const rust = mat("#8b6f55");
  if (kind === "bedroom") {
    const bedZ = z1 - 1.2;
    box(scene, dark, cx, 0.1, bedZ, 1.95, 0.12, 2.15);
    box(scene, linen, cx, 0.24, bedZ + 0.04, 1.82, 0.16, 1.95);
    box(scene, wood, cx, 0.55, z1 - 0.18, 1.95, 0.86, 0.08);
    box(scene, pillow, cx - 0.38, 0.38, z1 - 0.55, 0.58, 0.12, 0.36);
    box(scene, pillow, cx + 0.38, 0.38, z1 - 0.55, 0.58, 0.12, 0.36);
    box(scene, wood, x0 + 0.48, 0.28, z0 + 0.55, 0.48, 0.08, 0.4);
    for (const [lx, lz] of [[-0.18, -0.14], [0.18, -0.14], [-0.18, 0.14], [0.18, 0.14]]) {
      cyl(scene, dark, x0 + 0.48 + lx, 0.14, z0 + 0.55 + lz, 0.03, 0.26);
    }
    cyl(scene, mat("#f0e6c8", { emissive: "#f0e6c8", emissiveIntensity: 0.2 }), x0 + 0.48, 0.5, z0 + 0.55, 0.08, 0.12);
  } else if (kind === "living") {
    const w = Math.min(2.5, x1 - x0 - 0.7);
    box(scene, dark, cx, 0.1, z1 - 0.72, w, 0.1, 0.82);
    box(scene, seat, cx, 0.26, z1 - 0.78, w - 0.16, 0.16, 0.62);
    box(scene, cloth, cx, 0.52, z1 - 0.38, w, 0.48, 0.14);
    box(scene, cloth, cx - w / 2 + 0.08, 0.38, z1 - 0.72, 0.14, 0.36, 0.82);
    box(scene, cloth, cx + w / 2 - 0.08, 0.38, z1 - 0.72, 0.14, 0.36, 0.82);
    box(scene, wood, cx, 0.28, cz, 0.95, 0.05, 0.55);
    for (const [lx, lz] of [[-0.38, -0.2], [0.38, -0.2], [-0.38, 0.2], [0.38, 0.2]]) {
      cyl(scene, dark, cx + lx, 0.14, cz + lz, 0.03, 0.26);
    }
  } else if (kind === "dining") {
    box(scene, wood, cx, 0.72, cz, 1.55, 0.05, 0.92);
    for (const [lx, lz] of [[-0.62, -0.34], [0.62, -0.34], [-0.62, 0.34], [0.62, 0.34]]) {
      cyl(scene, dark, cx + lx, 0.36, cz + lz, 0.035, 0.7);
    }
    chair(scene, wood, seat, cx, cz + 0.72, 0);
    chair(scene, wood, seat, cx, cz - 0.72, Math.PI);
    chair(scene, wood, seat, cx - 0.95, cz, Math.PI / 2);
    chair(scene, wood, seat, cx + 0.95, cz, -Math.PI / 2);
  } else if (kind === "kitchen") {
    const w = Math.max(1.8, x1 - x0 - 0.55);
    box(scene, wood, cx, 0.4, z1 - 0.38, w, 0.8, 0.52);
    box(scene, top, cx, 0.82, z1 - 0.38, w + 0.04, 0.05, 0.56);
    box(scene, stone, cx, 1.15, z1 - 0.16, w, 0.55, 0.08);
    box(scene, dark, cx - w / 4, 0.84, z1 - 0.38, 0.42, 0.04, 0.32);
    box(scene, wood, x1 - 0.32, 1.05, cz, 0.42, 2.0, Math.min(1.6, z1 - z0 - 0.7));
  } else if (kind === "bath") {
    box(scene, porcelain, x0 + 0.62, 0.22, z1 - 0.75, 0.78, 0.36, 1.35);
    box(scene, water, x0 + 0.62, 0.38, z1 - 0.75, 0.62, 0.04, 1.15);
    cyl(scene, porcelain, x1 - 0.48, 0.2, z0 + 0.58, 0.22, 0.2);
    box(scene, porcelain, x1 - 0.48, 0.52, z0 + 0.38, 0.38, 0.36, 0.16);
    box(scene, porcelain, x1 - 0.48, 0.34, z0 + 0.58, 0.3, 0.04, 0.3);
  } else if (kind === "hallway") {
    box(scene, rust, cx, 0.015, cz, Math.max(1.1, x1 - x0 - 0.45), 0.02, 0.62);
  } else if (kind === "closet") {
    const w = Math.max(0.9, x1 - x0 - 0.28);
    box(scene, wood, cx, 0.95, z1 - 0.1, w, 1.85, 0.06);
    for (const y of [0.45, 0.95, 1.45]) box(scene, wood, cx, y, z1 - 0.22, w - 0.08, 0.04, 0.28);
    cyl(scene, dark, cx, 1.55, z1 - 0.28, 0.012, w - 0.2, "x");
  }
}

function floorColor(kind: string) {
  return ({
    hallway: "#9a8b78",
    living: "#c4b7a2",
    dining: "#c9b89a",
    kitchen: "#b8b3a8",
    bedroom: "#cfc2ae",
    bath: "#c5cdc8",
    closet: "#b7ada0",
    room: "#cfc6b6",
  } as Record<string, string>)[kind] || "#cfc6b6";
}

function wallOf(pts: [number, number][], x: number, z: number) {
  const { x0, x1, z0, z1 } = bounds(pts);
  const faces = [
    { dist: Math.abs(z - z0), x, z: z0 + 0.05, nx: 0, nz: -1 },
    { dist: Math.abs(z - z1), x, z: z1 - 0.05, nx: 0, nz: 1 },
    { dist: Math.abs(x - x0), x: x0 + 0.05, z, nx: -1, nz: 0 },
    { dist: Math.abs(x - x1), x: x1 - 0.05, z, nx: 1, nz: 0 },
  ];
  return faces.sort((a, b) => a.dist - b.dist)[0];
}

function addStain(
  scene: InstanceType<typeof THREE.Scene>,
  targets: { userData: { hid?: string } }[],
  hid: string,
  color: string,
  x: number,
  y: number,
  z: number,
  pts: [number, number][],
) {
  const face = wallOf(pts, x, z);
  const geo = new THREE.SphereGeometry(0.34, 14, 12);
  geo.scale(1.25, 0.95, 0.07);
  const material = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: 0.22,
    roughness: 0.92,
    transparent: true,
    opacity: 0.9,
  });
  const stain = new THREE.Mesh(geo, material);
  stain.position.set(face.x, y, face.z);
  stain.lookAt(face.x + face.nx, y, face.z + face.nz);
  stain.userData.hid = hid;
  scene.add(stain);
  targets.push(stain);
}

export function Model({
  rooms,
  colorOf,
  labelOf,
  onPick,
}: {
  rooms: Room[];
  colorOf: (id: string) => string;
  labelOf: (id: string) => string;
  onPick: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const pick = useRef(onPick);
  pick.current = onPick;
  const key = rooms.map((r) => r.id + r.name + JSON.stringify(r.polygon) + r.hazards.map((h) => h.hazard_id).join(",")).join("|");

  useEffect(() => {
    const el = ref.current;
    if (!el || rooms.length === 0) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#e6e0d4");
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.setAttribute("aria-label", "Building");
    el.appendChild(renderer.domElement);

    const labels = new CSS2DRenderer();
    labels.domElement.style.position = "absolute";
    labels.domElement.style.inset = "0";
    labels.domElement.style.pointerEvents = "none";
    el.appendChild(labels.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const sun = new THREE.DirectionalLight(0xfff4e0, 0.7);
    sun.position.set(6, 12, 8);
    scene.add(sun);

    const wall = new THREE.MeshStandardMaterial({ color: "#f6f2ea" });
    const targets: { userData: { hid?: string } }[] = [];
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    rooms.forEach((room) => {
      const pts = ring(room.polygon);
      if (pts.length < 3) return;
      pts.forEach((p) => {
        minX = Math.min(minX, p[0]);
        maxX = Math.max(maxX, p[0]);
        minZ = Math.min(minZ, p[1]);
        maxZ = Math.max(maxZ, p[1]);
      });
      const h = room.height;
      const kind = kindOf(room.name);
      const { x0, x1, z0, z1 } = bounds(pts);
      addFloor(scene, pts, 0.04, new THREE.MeshStandardMaterial({ color: floorColor(kind) }));
      addFloor(scene, pts, h, new THREE.MeshStandardMaterial({ color: "#b7ad9e", transparent: true, opacity: 0.28 }));
      const gapAt = room.door?.edge !== undefined || room.door ? doorEdge(pts, room.door || {}) : -1;
      const gapWidth = room.door?.width || 0.9;
      const gapCenter = room.door?.center ?? 0.5;
      pts.forEach((p, i) => {
        const q = pts[(i + 1) % pts.length];
        const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
        if (i === gapAt && gapWidth < len - 0.05) {
          const g0 = Math.max(0, gapCenter * len - gapWidth / 2);
          const g1 = Math.min(len, gapCenter * len + gapWidth / 2);
          addSegment(scene, wall, p[0], p[1], q[0], q[1], h, 0, g0);
          addSegment(scene, wall, p[0], p[1], q[0], q[1], h, g1, len);
        } else {
          addSegment(scene, wall, p[0], p[1], q[0], q[1], h, 0, len);
        }
      });
      furnish(scene, kind, x0, z0, x1, z1);

      const ordered = [...room.hazards].sort((p, q) => p.icon[0] - q.icon[0] || p.icon[2] - q.icon[2]);
      ordered.forEach((hz, index) => {
        const [x, y, z] = hz.icon;
        const color = colorOf(hz.hazard_id);
        addStain(scene, targets, hz.hazard_id, color, x, y, z, pts);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "marker-label";
        button.textContent = twoLines(labelOf(hz.hazard_id) || "Hazard");
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          pick.current(hz.hazard_id);
        });
        const holder = document.createElement("div");
        holder.style.pointerEvents = "none";
        button.dataset.base = ordered.length > 1 ? (index % 2 === 0 ? "-78%" : "78%") : "0px";
        holder.appendChild(button);
        const face = wallOf(pts, x, z);
        const tag = new CSS2DObject(holder);
        tag.position.set(face.x, y + 0.42, face.z);
        scene.add(tag);
      });
    });

    const focusX = Number.isFinite(minX) ? (minX + maxX) / 2 : 0;
    const focusZ = Number.isFinite(minZ) ? (minZ + maxZ) / 2 : 2;
    camera.position.set(focusX, 9.2, minZ - 5.2);
    camera.lookAt(focusX, 0.3, focusZ);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(focusX, 0.3, focusZ);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 4;
    controls.maxDistance = 22;
    controls.maxPolarAngle = Math.PI / 2.15;
    controls.update();

    const resize = () => {
      const width = el.clientWidth || 320;
      const height = el.clientHeight || 280;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      labels.setSize(width, height);
    };
    resize();

    const ray = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let downX = 0;
    let downY = 0;
    const onDown = (ev: PointerEvent) => {
      downX = ev.clientX;
      downY = ev.clientY;
    };
    const onUp = (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      ray.setFromCamera(pointer, camera);
      const hit = ray.intersectObjects(targets as never[])[0] as { object?: { userData?: { hid?: string } } } | undefined;
      if (hit?.object?.userData?.hid) pick.current(hit.object.userData.hid);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);
    let frame = 0;
    const loop = () => {
      frame = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
      separateLabels(el);
    };
    loop();
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === el) el.removeChild(renderer.domElement);
      if (labels.domElement.parentElement === el) el.removeChild(labels.domElement);
    };
    // key captures the room layout; colorOf and labelOf are read when the scene is built
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return <div ref={ref} className="model" />;
}
