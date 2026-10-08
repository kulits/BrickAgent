# Viewer
`viewer.js` supplies the shared LDraw loading, materials, instancing, lighting, and camera.
`index.html` runs it in a browser; `view.mjs` runs it in Node and saves a PNG.

`LDrawLoader.js` is three.js's LDraw loader with one performance change, under its MIT license.
Three.js itself, the native WebGL implementation (`webgl-node`), and PNG encoding (`pngjs`) come
from npm; their source and binaries are not in BrickAgent's release.
The manifest and lockfile live here. Install with Node 22.14+ (22.x) or 23.6+ and npm:

```sh
# Source checkout:
npm ci --prefix src/brickagent/viewer

# Installed Python package:
npm ci --prefix "$(python -c 'from importlib.resources import files; print(files("brickagent") / "viewer")')"
```

Node PNG output needs an EGL/GLES runtime and the C++ runtime required by `native-gles`.
On Linux, Mesa's `libegl1`, `libgles2`, `libegl-mesa0`, and `libgl1-mesa-dri` provide software
rendering when GPU drivers are unavailable. The browser uses its own WebGL implementation.

## Python
```python
from brickagent import view

view(model, "model.png")
view(model, "side.png", az=90, el=10, width=1200, height=800)
```

Set `BRICKAGENT_LDRAW=/path/to/ldraw`, containing the original `parts/` and `p/` directories,
or pass `ldraw=...` to `view`. `BRICKAGENT_VIEWER` is a single executable path, defaulting to
`node` on PATH. A launcher must forward arguments unchanged and make the package, model,
output, npm dependencies, and LDraw library accessible to its runtime.

Python passes these arguments separately, without shell parsing:

```text
view.mjs model.mpd output.png az el width height colors.json ldraw
```

Paths are absolute. An omitted `az` or `el` is an empty argument selecting the automatic angle.
Explicit angles are finite degrees; dimensions are positive integer pixels. Each call waits
for completion and releases its process and temporary files. Errors propagate to Python.
The script also accepts geometry-packed MPDs, for which the final `ldraw` argument is optional.

## Browser
`index.html` is an interactive viewer for people. It needs the same `npm ci` as above and must be served
over HTTP from inside the package; the model and LDraw library can live anywhere on the same server:

```sh
mkdir www && cd www
ln -s "$(python -c 'from importlib.resources import files; print(files("brickagent"))')" brickagent
ln -s /path/to/ldraw ldraw
cp /path/to/model.mpd .
python -m http.server 8000
```

Then open `http://localhost:8000/brickagent/viewer/index.html?model=/model.mpd&ldraw=/ldraw/`. The `ldraw`
URL must end in `/`. Drag to orbit, right-drag to pan, scroll to zoom, and press F to fit.

Both modes use original geometry, a black background, surface colors, and transparency.
PNG output uses full geometry and four-sample antialiasing. The interactive viewer uses boxes
for parts projected below three pixels. LDraw lines and conditional edges are not drawn.
