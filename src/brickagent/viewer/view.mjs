import fs from 'node:fs';
import { createWebGL2Context } from 'webgl-node';
import { PNG } from 'pngjs';
import { createViewer } from './viewer.js';
import { LoadingManager, WebGLRenderTarget, SRGBColorSpace } from 'three';
import { LDrawLoader } from './LDrawLoader.js';
const [input, output, az, el, width, height, palette, ldraw = ''] = process.argv.slice(2);
const [w, h] = [width, height].map(Number);
if (![w, h].every(v => Number.isSafeInteger(v) && v > 0)) {
  throw new RangeError('width and height must be positive integer pixels');
}
if (![az, el].every(v => v === '' || Number.isFinite(Number(v)))) {
  throw new RangeError('az and el must be finite degrees or omitted');
}

// Three.js emits browser progress events while reading files in Node.
globalThis.ProgressEvent ??= class extends Event {
  constructor(type, init) {
    super(type);
    Object.assign(this, init);
  }
};
const manager = new LoadingManager().setURLModifier(file =>
  `data:text/plain;base64,${fs.readFileSync(file).toString('base64')}`,
);
const loader = new LDrawLoader(manager).setPartsLibraryPath(ldraw ? `${ldraw}/` : '');
const { canvas, gl, destroy } = createWebGL2Context(w, h);
const container = { clientWidth: w, clientHeight: h, appendChild() {} };
const viewer = createViewer(container, { canvas, context: gl, interactive: false });
await viewer.load(fs.readFileSync(input, 'utf8'), JSON.parse(fs.readFileSync(palette, 'utf8')).colors, loader);
viewer.frame(az === '' ? undefined : Number(az), el === '' ? undefined : Number(el));
const target = new WebGLRenderTarget(w, h, { samples: 4 });
target.texture.colorSpace = SRGBColorSpace;
viewer.renderer.setRenderTarget(target);
viewer.render();
const stride = w * 4, pixels = Buffer.alloc(stride * h);
viewer.renderer.readRenderTargetPixels(target, 0, 0, w, h, pixels);
const png = new PNG({ width: w, height: h });
for (let y = 0; y < h; y++) {
  pixels.copy(png.data, (h - 1 - y) * stride, y * stride, (y + 1) * stride);
}
fs.writeFileSync(output, PNG.sync.write(png));
destroy();
