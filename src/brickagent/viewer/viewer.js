import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { LDrawLoader } from './LDrawLoader.js';
import { LDrawConditionalLineMaterial } from 'three/addons/materials/LDrawConditionalLineMaterial.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

/** Native MPD definitions and placed part instances. */
export function readMPD(text) {
  const files = new Map();
  let name = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('0 FILE ')) {
      name = line.slice(7).trim().toLowerCase();
      files.set(name, []);
    } else if (name) {
      files.get(name).push(line);
    }
  }
  const placements = [];
  const pending = [{ name: files.keys().next().value, matrix: new THREE.Matrix4(), color: 7 }];
  while (pending.length) {
    const item = pending.pop();
    if (item.name.endsWith('.dat')) {
      placements.push(item);
      continue;
    }
    if (!files.has(item.name)) throw new Error(`Missing MPD definition: ${item.name}`);
    const children = [];
    for (const line of files.get(item.name)) {
      if (!line.startsWith('1 ')) continue;
      const fields = line.trim().split(/\s+/);
      const values = fields.slice(2, 14).map(Number);
      const name = fields.slice(14).join(' ').replaceAll('\\', '/').toLowerCase();
      const color = [16, 24].includes(Number(fields[1])) ? item.color : Number(fields[1]);
      const local = new THREE.Matrix4().set(
        values[3], values[4], values[5], values[0],
        values[6], values[7], values[8], values[1],
        values[9], values[10], values[11], values[2],
        0, 0, 0, 1,
      );
      const matrix = item.matrix.clone().multiply(local);
      children.push({ name, color, matrix });
    }
    for (const child of children.reverse()) pending.push(child);
  }
  return { files, placements };
}

/** Parse one copy of each part, preserving inherited and fixed LDraw colors. */
async function geometry(files, names, palette, loader) {
  loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
  loader.addDefaultMaterials();
  let text = '0 FILE vocabulary.ldr\n';
  for (const color of palette) {
    text += `0 !COLOUR C${color.code} CODE ${color.code} VALUE ${color.hex} EDGE ${color.edge}\n`;
  }
  for (const name of names) text += `1 16 0 0 0 1 0 0 0 1 0 0 0 1 ${name}\n`;
  for (const [name, lines] of files) {
    if (name.endsWith('.dat')) text += `0 FILE ${name}\n${lines.join('\n')}\n`;
  }
  const loaded = await new Promise((resolve, reject) => loader.parse(text, resolve, reject));
  // The loader can swallow nested-part failures; reject any failed dependency request.
  await Promise.all(Object.values(loader.partsCache.parseCache._cache));
  const parts = new Map();
  for (const child of loaded.children) {
    const name = (child.name || child.userData.fileName).toLowerCase();
    if (!names.has(name)) continue;
    child.updateWorldMatrix(true, true);
    const pieces = new Map();
    child.traverse(node => {
      if (!node.isMesh) return;
      const source = node.geometry.index ? node.geometry.toNonIndexed() : node.geometry.clone();
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      const groups = source.groups.length ? source.groups : [{
        start: 0,
        count: source.attributes.position.count,
        materialIndex: 0,
      }];
      for (const group of groups) {
        const code = Number(materials[group.materialIndex].userData.code);
        const part = new THREE.BufferGeometry();
        for (const attribute of ['position', 'normal']) {
          const array = source.attributes[attribute].array.slice(group.start * 3, (group.start + group.count) * 3);
          part.setAttribute(attribute, new THREE.BufferAttribute(array, 3));
        }
        part.applyMatrix4(node.matrixWorld);
        if (node.matrixWorld.determinant() < 0) {
          for (const attribute of Object.values(part.attributes)) {
            const a = attribute.array;
            for (let i = 0; i < a.length; i += 9) {
              for (let j = 0; j < 3; j++) {
                [a[i + 3 + j], a[i + 6 + j]] = [a[i + 6 + j], a[i + 3 + j]];
              }
            }
          }
        }
        // Uncertified double-sided faces can cancel during LDraw smoothing.
        if (!part.attributes.normal.array.some(Boolean)) part.computeVertexNormals();
        if (!pieces.has(code)) pieces.set(code, []);
        pieces.get(code).push(part);
      }
      source.dispose();
    });
    parts.set(name, [...pieces].map(([code, pieces]) => {
      const merged = mergeGeometries(pieces);
      pieces.forEach(piece => piece.dispose());
      merged.computeBoundingBox();
      return { code, geometry: merged };
    }));
  }
  for (const name of names) {
    if (!parts.get(name)?.length) throw new Error(`Missing geometry: ${name}`);
  }
  return parts;
}

/** Shared geometry, lighting and camera fitting for browser and Node views. */
export function createViewer(container, { canvas, context, interactive = true } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, canvas, context });
  container.appendChild(renderer.domElement);
  renderer.setPixelRatio(1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555566, 1));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2);
  keyLight.position.set(-3, 5, 4);
  scene.add(keyLight);
  const environment = new THREE.PMREMGenerator(renderer);
  scene.environment = environment.fromScene(new RoomEnvironment(), .04).texture;
  environment.dispose();
  const camera = new THREE.PerspectiveCamera(45, 1, 1, 200000);
  const controls = new OrbitControls(camera, interactive ? renderer.domElement : null);
  controls.enableDamping = false;
  const root = new THREE.Group();
  root.rotation.x = Math.PI;
  scene.add(root);
  const bounds = new THREE.Box3();
  const corners = [];
  const lod = [];
  let scheduled = false;

  function render() {
    const pixels = container.clientHeight / (2 * Math.tan(camera.fov * Math.PI / 360));
    for (const entry of lod) {
      const detailed = entry.size * pixels / camera.position.distanceTo(entry.center) > 3;
      entry.detail.visible = detailed;
      entry.proxy.visible = !detailed;
    }
    renderer.render(scene, camera);
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      render();
    });
  }

  function resize() {
    renderer.setSize(container.clientWidth, container.clientHeight);
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    if (interactive) schedule();
  }
  resize();
  if (interactive) {
    controls.addEventListener('change', schedule);
    new ResizeObserver(resize).observe(container);
  }

  function frame(az, el) {
    if (!corners.length) return;
    const center = corners.reduce((sum, p) => sum.add(p), new THREE.Vector3()).divideScalar(corners.length);
    if (az == null) {
      let xx = 0, xz = 0, zz = 0;
      for (const p of corners) {
        const x = p.x - center.x, z = p.z - center.z;
        xx += x * x; xz += x * z; zz += z * z;
      }
      const bearing = .5 * THREE.MathUtils.radToDeg(Math.atan2(2 * xz, xx - zz));
      const views = [-30, 30, 150, 210].map(a => (a - bearing + 540) % 360 - 180);
      // Stay near the usual three-quarter view while exposing an elongated model's broad side.
      az = Math.hypot(xx - zz, 2 * xz) < (xx + zz) / 3 ? 30
        : views.sort((a, b) => Math.abs(a - 30) - Math.abs(b - 30))[0];
    }
    const size = bounds.getSize(new THREE.Vector3());
    el ??= THREE.MathUtils.clamp(Math.atan2(Math.min(size.x, size.z), 2 * size.y) * 180 / Math.PI, 22, 60);
    const direction = new THREE.Vector3(
      Math.cos(el * Math.PI / 180) * Math.sin(az * Math.PI / 180),
      Math.sin(el * Math.PI / 180),
      Math.cos(el * Math.PI / 180) * Math.cos(az * Math.PI / 180),
    );
    const right = new THREE.Vector3(Math.cos(az * Math.PI / 180), 0, -Math.sin(az * Math.PI / 180));
    const up = new THREE.Vector3().crossVectors(direction, right);
    const points = corners.map(p => {
      const q = p.clone().sub(center);
      return new THREE.Vector3(q.dot(right), q.dot(up), q.dot(direction));
    });
    const vertical = Math.tan(camera.fov * Math.PI / 360);
    const horizontal = vertical * camera.aspect;
    let left = -Infinity, rightmost = -Infinity, bottom = -Infinity, top = -Infinity, nearest = -Infinity;
    for (const p of points) {
      left = Math.max(left, -p.x / horizontal + p.z);
      rightmost = Math.max(rightmost, p.x / horizontal + p.z);
      bottom = Math.max(bottom, -p.y / vertical + p.z);
      top = Math.max(top, p.y / vertical + p.z);
      nearest = Math.max(nearest, p.z);
    }
    const distance = 1.1 * Math.max(1, (left + rightmost) / 2, (bottom + top) / 2);
    // Center the perspective silhouette, including depth differences between its extremes.
    for (const [axis, basis] of [['x', right], ['y', up]]) {
      let lo = Infinity, hi = -Infinity;
      for (const p of points) { lo = Math.min(lo, p[axis]); hi = Math.max(hi, p[axis]); }
      for (let i = 0; i < 24; i++) {
        const mid = (lo + hi) / 2;
        let min = Infinity, max = -Infinity;
        for (const p of points) {
          const v = (p[axis] - mid) / (distance - p.z);
          min = Math.min(min, v); max = Math.max(max, v);
        }
        if (min + max > 0) lo = mid; else hi = mid;
      }
      center.addScaledVector(basis, (lo + hi) / 2);
    }
    camera.position.copy(center).addScaledVector(direction, distance);
    camera.near = Math.max(.0001, Math.min(distance / 5000, (distance - nearest) / 2));
    camera.far = Math.max(1000, distance * 30);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
  }

  async function load(text, palette, loader = new LDrawLoader()) {
    const { files, placements } = readMPD(text);
    const parts = await geometry(files, new Set(placements.map(p => p.name)), palette, loader);
    const colors = new Map(palette.map(p => [p.code, p]));
    const boxes = new Map([...parts].map(([name, pieces]) =>
      [name, pieces.reduce((box, piece) => box.union(piece.geometry.boundingBox), new THREE.Box3())]));
    const flip = new THREE.Matrix4().makeRotationX(Math.PI);
    const materials = new Map();
    const batches = new Map();
    for (const part of placements) {
      const box = boxes.get(part.name);
      for (const x of [box.min.x, box.max.x]) {
        for (const y of [box.min.y, box.max.y]) {
          for (const z of [box.min.z, box.max.z]) {
            corners.push(new THREE.Vector3(x, y, z).applyMatrix4(part.matrix).applyMatrix4(flip));
          }
        }
      }
      const position = new THREE.Vector3().setFromMatrixPosition(part.matrix);
      const key = [Math.floor(position.x / 1280), Math.floor(position.z / 1280), part.name, part.color].join('|');
      if (!batches.has(key)) batches.set(key, []);
      batches.get(key).push(part);
    }
    for (const instances of batches.values()) {
      const part = instances[0];
      for (const piece of parts.get(part.name)) {
        const code = piece.code === 16 ? part.color : piece.code;
        const color = colors.get(code);
        const opacity = (color?.alpha ?? 255) / 255;
        if (!materials.has(code)) {
          materials.set(code, new THREE.MeshStandardMaterial({
            color: code >= 0x2000000 ? code & 0xffffff : color?.hex || '#9ba19d',
            roughness: .42,
            metalness: color?.material === 'chrome' ? 1 : 0,
            opacity,
            transparent: opacity < 1,
            depthWrite: opacity === 1,
          }));
        }
        const material = materials.get(code);
        const detail = new THREE.InstancedMesh(piece.geometry, material, instances.length);
        const world = new THREE.Box3();
        for (let i = 0; i < instances.length; i++) {
          detail.setMatrixAt(i, instances[i].matrix);
          world.union(piece.geometry.boundingBox.clone().applyMatrix4(instances[i].matrix));
        }
        detail.instanceMatrix.needsUpdate = true;
        root.add(detail);
        world.applyMatrix4(flip);
        bounds.union(world);
        if (interactive) {
          const size = piece.geometry.boundingBox.getSize(new THREE.Vector3());
          const center = piece.geometry.boundingBox.getCenter(new THREE.Vector3());
          const box = new THREE.BoxGeometry(size.x, size.y, size.z).translate(center.x, center.y, center.z);
          const proxy = new THREE.InstancedMesh(box, material, instances.length);
          proxy.instanceMatrix = detail.instanceMatrix;
          root.add(proxy);
          lod.push({ detail, proxy, size: size.length(), center: world.getCenter(new THREE.Vector3()) });
        }
      }
    }
    return placements.length;
  }
  return { load, frame, render, camera, controls, scene, renderer };
}
