declare module "three";

declare module "three/examples/jsm/controls/OrbitControls.js" {
  export class OrbitControls {
    constructor(camera: object, domElement?: HTMLElement);
    target: { set: (x: number, y: number, z: number) => void };
    enableDamping: boolean;
    dampingFactor: number;
    minDistance: number;
    maxDistance: number;
    maxPolarAngle: number;
    update: () => void;
    dispose: () => void;
  }
}

declare module "three/examples/jsm/renderers/CSS2DRenderer.js" {
  export class CSS2DRenderer {
    domElement: HTMLElement;
    setSize: (width: number, height: number) => void;
    render: (scene: object, camera: object) => void;
  }
  export class CSS2DObject {
    constructor(element: HTMLElement);
    position: { set: (x: number, y: number, z: number) => void };
  }
}
