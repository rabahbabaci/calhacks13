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
  const key = rooms.map((r) => r.id + r.hazards.map((h) => h.hazard_id).join(",")).join("|");

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
    rooms.forEach((room) => {
      const [a, b, , d] = room.polygon;
      const x0 = a[0];
      const z0 = a[1];
      const w = b[0] - a[0];
      const depth = d[1] - a[1];
      const h = room.height;
      const floor = new THREE.Mesh(
        new THREE.BoxGeometry(w, 0.08, depth),
        new THREE.MeshStandardMaterial({ color: "#cfc6b6" }),
      );
      floor.position.set(x0 + w / 2, 0.04, z0 + depth / 2);
      scene.add(floor);
      const north = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.08), wall);
      north.position.set(x0 + w / 2, h / 2, z0 + depth);
      scene.add(north);
      const east = new THREE.Mesh(new THREE.BoxGeometry(0.08, h, depth), wall);
      east.position.set(x0 + w, h / 2, z0 + depth / 2);
      scene.add(east);
      const west = east.clone();
      west.position.set(x0, h / 2, z0 + depth / 2);
      scene.add(west);
      const side = (w - room.door.width) / 2;
      const left = new THREE.Mesh(new THREE.BoxGeometry(side, h, 0.08), wall);
      left.position.set(x0 + side / 2, h / 2, z0);
      scene.add(left);
      const right = left.clone();
      right.position.set(x0 + w - side / 2, h / 2, z0);
      scene.add(right);
      const ceil = new THREE.Mesh(
        new THREE.BoxGeometry(w, 0.04, depth),
        new THREE.MeshStandardMaterial({ color: "#b7ad9e", transparent: true, opacity: 0.28 }),
      );
      ceil.position.set(x0 + w / 2, h, z0 + depth / 2);
      scene.add(ceil);

      const ordered = [...room.hazards].sort((p, q) => p.icon[0] - q.icon[0]);
      ordered.forEach((hz, index) => {
        const [x, y, z0] = hz.icon;
        const z = z0 - 0.45;
        const color = colorOf(hz.hazard_id);
        const material = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.45 });
        const post = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.05, 0.55, 8), material);
        post.position.set(x, y - 0.05, z);
        post.userData.hid = hz.hazard_id;
        const head = new THREE.Mesh(new THREE.SphereGeometry(0.16, 20, 16), material);
        head.position.set(x, y + 0.32, z);
        head.userData.hid = hz.hazard_id;
        scene.add(post, head);
        targets.push(post, head);

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
        const tag = new CSS2DObject(holder);
        tag.position.set(x, y + 0.72, z);
        scene.add(tag);
      });
    });

    const span = Math.max(rooms.length * 4.6, 4);
    const focusX = span / 2 - 0.4;
    camera.position.set(focusX, 5.4, -8);
    camera.lookAt(focusX, 1.2, 2);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(focusX, 1.2, 2);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 4;
    controls.maxDistance = 16;
    controls.maxPolarAngle = Math.PI / 2 - 0.08;
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
