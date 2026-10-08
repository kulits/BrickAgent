Your task is to design a high-quality LEGO set depicting:
{{ object_prompt }}

# Building with BrickAgent
BrickAgent is a Python toolkit for programmatic LEGO design. It uses LDraw, a digital part library and format for
representing part identity, color, and pose. Its output is .mpd which contains the model and nested subassemblies.

## BrickNet
BrickNet represents builds as graphs of connected parts. BrickAgent uses its connector labels to compute placements.

| Connector pair | Fit |
| --- | --- |
| `stud` / `open` <-> `hole` / `tube` | `yaw` about the stud axis |
| `open` <-> `post` | Hollow stud over a post |
| `pin` / `axle` <-> `socket` | Round Technic hole; `flip`, `yaw`, and `slide` |
| `axle` <-> `cross` | Cross-shaped hole; `flip`, `yaw`, and `slide` |
| `bar` <-> `clip` / `cross` | Round bar; `flip`, `yaw`, and `slide` |
| `hinge` <-> `hinge` | `flip` and `yaw` |
| `ball` <-> `ball` | Rotate freely about the ball center |
| `fixed` <-> `fixed` | Predetermined pose |

## Construction
An `Assembly` is mutable and owns a local frame. `add` places a child; `attach` connects one through the BrickNet
connector system. Both append and return an immutable placed `Occurrence`. Either LDraw part ids or names can be used.

```python
from brickagent import Assembly, ID, at, check

model = Assembly("bridge")
left = model.add("brick 1x1", color="tan")
right = model.add("brick 1x1", pose=at(x=2), color="tan")
span = model.attach("plate 1x3", to=left.stud[0], by=("hole", 0), orient=ID, color="red")
check(model, contacts=[(span.hole[2], right.stud[0])])
model.write("bridge.mpd")
```

`attach()` records the join to the left support. The optional `contacts` argument checks the join to the right support;
use it when a piece spans supports or closes a loop.

`to` belongs to the receiving assembly. For a source part, `by=(type, index)` selects its connector; omission requires
a unique compatible source connector. Attachments do not move existing parts. `placement(target, source, ...)` computes
a pose without insertion. A part placed with `add` must land exactly on the stud grid of whatever it rests on; `attach`
does that for you.

Native coordinates: X/Z horizontal, **−Y up**. `add` defaults to the native identity pose. `move(x, y, z)` uses LDraw
units (LDU); `at(x, y, z)` uses studs/plates/studs (20/8/20 LDU). `spin(axis, degrees)` makes a right-handed rotation.
Studs are not at multiples of 20 LDU from every origin: a 2x4 has them at x=+/-10, +/-30 and a 1x3 at x=-20, 0, 20,
so an `add` pose on the 20 LDU grid seats only where the two parts' stud positions coincide; `attach` and `.at` take
care of that. Poses compose with `@` from right to left: `at(x=1) @ spin(UP, 90)` turns a part, then moves it. A pose
places a part's origin, which is not its bounds and differs between parts; `measure` gives its bounds, and `attach`
needs neither.

## Connectors and Orientation
`part.stud[i]`, `part.socket[i]`, etc. use zero-based, per-type BrickNet indices; negative indices work. For unfamiliar
parts, `print(describe(stem, type="socket"))` shows these same addresses. Stud, hole, and axle connectors are addressed
by their subtype, the names in the table above: `part.stud[i]` are solid studs and `part.open[i]` hollow ones;
`part.hole[i]`, `part.tube[i]`, and `part.post[i]` are the three subtypes of hole, of which only a post is limited to
hollow studs. Hinge, ball, and fixed connectors are addressed by kind; `describe` shows each one's subtype and polarity,
and a pair fits when the subtypes match and the polarities are opposite (`in` to `on`). `compatible(a, b)` checks
pairing. Axle connectors join while they overlap along the axis by the smaller of 4 LDU and the shorter connector's full
length. `describe` gives each one's `half-length` and `slide=0` puts their centers together, so `slide` can reach the
two half-lengths added, minus that overlap, in either direction: a wheel rim (half-length 10) on a 4L axle (half-length
37.5) takes `slide` from −43.5 to 43.5. A connector in a model carries `.at` and `.axis` in the model's frame, so
`brick.stud[5].at` is that stud's position after rotation and `brick.stud.near((x, y, z))` the stud closest to a point.

Standard rectangular bricks run along X: a 2×N stud layout has `stud[2*i]` and `stud[2*i+1]` at column `i`. Indices are
part-local, so a rotated part keeps its numbering. Straight Technic beams run along Z, with sockets in increasing Z
order. Pins 2780 and 6558 run along +X from `pin[0]` toward `pin[1]` (short/long on 6558).

| Attachment Option | Meaning |
| --- | --- |
| `yaw` | Relative, right-handed degrees about the target axis; zero follows its connector roll. |
| `align=(u, v)` | Point source-local `u` along assembly direction `v`, selecting the flip, then apply `yaw`. |
| `orient=pose` | Full source orientation in the receiving assembly; connectors determine position. |
| `flip` | `True` opposes the connector axes; omitted, the insertion direction is chosen automatically. |
| `slide` | Additional distance in LDU along the target connector's axis; axle connectors only. |

`orient=ID` keeps the source's native axes aligned with the receiving assembly, even when the target part is rotated.
`orient=spin(UP, 90)` sets a quarter turn in that assembly. Use `align` to aim a part along a direction; it computes the
joint rotation. Use `orient` separately from `align`/`yaw`. Orientations must respect joint freedoms: ball joints permit
arbitrary rotation; fixed pairs permit none.

Default `seat="auto"` handles recognized pin collars/socket recesses, including long pins and half-width beams.
`seat="center"`, `"+end"` or `"-end"` instead aligns labelled interval centers or ends. Labelled ends are not
necessarily physical stops; check clearance.

## Reuse
Return connectors with a subassembly. `placed.connector(reference)` selects that connector in one particular copy. To
attach the whole definition, pass its returned connector as `by`.

```python
from brickagent import Assembly, UP, at, check

def pinned_beam():
    unit = Assembly("pinned_beam")
    beam = unit.add("technic beam 5")
    pin = unit.attach("2780", to=beam.socket[-1], by=("pin", 0), align=((1, 0, 0), UP))
    return unit, pin.pin[1]

unit, pin_end = pinned_beam()
scene = Assembly("links")
left = scene.add(unit, color="yellow")
scene.add(unit, pose=at(x=8), color="blue")
scene.attach("technic beam 3", to=left.connector(pin_end), by=("socket", 0), flip=True, yaw=30)
check(scene, stability=False)
```

Repeated placements share the definition: later edits affect every copy. Use a fresh factory call for independent
variants. Colors inherit unless a child specifies one. `scene.add(left.mirror("x"))` reflects a placement through its
parent's X=0 plane, using native counterpart parts; sources remain unchanged. Mirroring a definition uses its own frame.

## Finding Parts
`find` ranks part ids for a query, `name` reads them back, and `describe` shows a part's connectors. Connectors on a
grid print as one row with the grid size and each axis's values; a placed connector's `.at` gives its frame position.
```python
from brickagent import describe, find, name

for query in ("torso safari shirt", "leg cargo pocket", "head smirk moustache", "fedora", "minifig axe"):
    print(query, [(stem, name(stem)) for stem in find(query)[:4]])
print(describe("3815b"))
print(describe("3001"))
```

```
torso safari shirt [('973pa6', 'minifig torso with safari shirt, blue tee, red bandana pattern'), ('973pq7', 'minifig torso with safari shirt, tan bandana & compass pattern'), ('973pa7', 'minifig torso with safari shirt, black neck and brown holster pattern'), ('973pax', 'minifig torso with safari shirt, gun, red bandana & white chest pattern')]
leg cargo pocket [('3817cpa3', 'minifig leg left with buttoned cargo pocket pattern'), ('3816cpa3', 'minifig leg right with buttoned cargo pocket pattern'), ('3817cpx2', 'minifig leg left with pocket pattern'), ('3816cp72', 'minifig leg right with zippered pocket pattern')]
head smirk moustache [('3626bpa3', 'minifig head with smirk & black moustache pattern'), ('3626bpa5', 'minifig head with stubble, moustache and smirk pattern'), ('3626bpap', 'minifig head with smirk, black moustache and cleft chin pattern'), ('685p03', 'maxifig head with face with moustache pattern')]
fedora [('61506', 'minifig hat fedora'), ('90538', 'minifig hat fedora with wide brim'), ('1849', 'minifig hat fedora outback style with wide brim with molded band and short hair')]
minifig axe [('95330', 'minifig axe'), ('30193', 'minifig ice axe'), ('3848', 'minifig battleaxe'), ('18788', 'minifig axe blocky')]
3815b: minifig hips
bounds: ((-18.0, -11.0, -10.0), (18.0, 20.750224701, 10.0)) LDU
connectors: at/half-length in LDU; axis=frame +Y; roll=frame +Z (part-local)
fixed[0] fixed sub=0 polarity=in | at=(0.0, 0.0, 0.0) | axis=(-1.0, 0.0, 0.0) | roll=(0.0, -1.0, 0.0)
fixed[1] fixed sub=0 polarity=in | at=(0.0, 0.0, 0.0) | axis=(1.0, 0.0, 0.0) | roll=(0.0, -1.0, 0.0)
hinge[0] hinge sub=1 polarity=in | at=(-2.0, 12.0, 0.0) | axis=(1.0, 0.0, 0.0) | roll=(0.0, 0.0, 1.0)
hinge[1] hinge sub=1 polarity=in | at=(2.0, 12.0, 0.0) | axis=(-1.0, 0.0, 0.0) | roll=(0.0, 0.0, 1.0)
3001: brick 2x4
bounds: ((-40.0, -4.0, -20.0), (40.0, 24.0, 20.0)) LDU
connectors: at/half-length in LDU; axis=frame +Y; roll=frame +Z (part-local)
hole[0..7] hole sub=hole | 4 x 2 grid | x=-30..30 step 20 | y=24 | z=-10, 10 | axis=(0.0, 1.0, 0.0) | roll=(0.0, 0.0, 1.0)
tube[0..2] hole sub=tube | 3 in a row | x=-20..20 step 20 | y=24 | z=0 | axis=(0.0, 1.0, 0.0) | roll=(0.0, 0.0, 1.0)
stud[0..7] stud sub=stud | 4 x 2 grid | x=-30..30 step 20 | y=0 | z=-10, 10 | axis=(0.0, 1.0, 0.0) | roll=(0.0, 0.0, 1.0)
```

```python
brick = Assembly("corner").add("3001", pose=at(x=3) @ spin(UP, 90))
print(brick.stud[5].at, brick.stud[5].axis)  # (50.0, 0.0, 10.0) (0.0, 1.0, 0.0): in the model's frame, after the turn
print(brick.stud.near((48, 0, 12)).address)  # stud[5]: pick a connector by where it is, not by its number
```

## SNOT
```python
print(find("brick 1x1 with side stud")[:2])  # ['87087', '4070']
print(describe("87087"))  # hole[0] at=(0.0, 24.0, 0.0) axis=(0.0, 1.0, 0.0); open[0] at=(0.0, 10.0, -10.0) axis=(0.0, 0.0, 1.0)
print(describe("6141"))  # tube[0] at=(0.0, 8.0, 0.0) axis=(0.0, 1.0, 0.0); stud[0] at=(0.0, 0.0, 0.0) axis=(0.0, 1.0, 0.0)
model = Assembly("side stud")
holder = model.add("87087", color="red")
model.attach("6141", to=holder.open[0], by=("tube", 0), color="trans clear")
check(model)
```

## Connector Compatibility
```python
print(describe("6014b"))  # wheel rim 12x11: hinge[0] hinge sub=3 polarity=on and fixed[0] fixed sub=13 polarity=in
socket = connectors("6014b", "hinge")[0]
holders = [s for s in find() if any(compatible(socket, c) for c in connectors(s, "hinge"))]
print([(s, name(s)) for s in holders])  # 37 parts: wheel pins, car bases, a wheelchair
seat = connectors("6014b", "fixed")[0]
tyres = [s for s in find("tyre") if any(compatible(seat, c) for c in connectors(s, "fixed"))]
mount = Assembly("mount")
plate = mount.add("2926", color="black")  # plate 1x4 with 2 wheel pins, from the list above
rim = mount.attach("6014b", to=plate.hinge[0], by=("hinge", 0), color="yellow")
mount.attach(tyres[0], to=rim.fixed[0], by=("fixed", 0), color="black")
```

## Posing a Minifigure
```python
from brickagent import Assembly, check

def johnny_thunder():
    fig = Assembly("Johnny Thunder")
    hips = fig.add("3815b", color="dark brown")
    fig.attach("3816cpa3", to=hips.hinge[0], by=("hinge", 0), color="dark brown")
    fig.attach("3817cpa3", to=hips.hinge[1], by=("hinge", 0), color="dark brown")
    torso = fig.attach("973pax", to=hips.fixed[1], by=("fixed", 0), color="white")
    hands = []
    for arm, shoulder, swing in (("3818", 0, 240), ("3819", 1, 230)):
        limb = fig.attach(arm, to=torso.hinge[shoulder], by=("hinge", 0), yaw=swing, color="white")
        hands.append(fig.attach("3820", to=limb.hinge[1], by=("hinge", 0), color="yellow"))
    head = fig.attach("3626bpa3", to=torso.pin[0], by=("socket", 0), yaw=20, color="yellow")
    fig.attach("61506", to=head.open[0], color="reddish brown")
    fig.attach("95330", to=hands[0].clip[0], by=("bar", 0), color="light bluish gray")
    return fig

check(johnny_thunder())
```

Shoulder angles run from a raised arm and mirror between sides. A held part follows its own bar axis, so `flip` turns
one end-for-end when it arrives the wrong way up.

## A Ball Joint
```python
from brickagent import Assembly, UP, check, spin

model = Assembly("ball joint")
base = model.add("22890", color="dark bluish gray")  # plate 1x2 with ball joint on end
arm = model.attach("14418", to=base.ball[0], by=("ball", 0), orient=spin(UP, 150), color="light bluish gray")
check(model)
```

`describe("22890")` lists its ball as `ball[0] ball sub=1 polarity=in` and `describe("14418")` its socket as
`polarity=on`: a ball pair needs the same subtype and opposite polarities. A ball joint permits any rotation, so give it
`orient` or `yaw`; the automatic insertion orientation is for stud and axle joints, and would lay this arm over its
base. Here a 150 degree turn about the vertical bends the arm 30 degrees from straight, and the parts stay clear.

## Workflow
- Discover with `find(query)`; filter ranked part numbers in Python. `name`, `measure`, and `connectors` expose part
  names, native LDraw bounds, and connector data.
- Render with `view(model, "preview.png")` and view the image; optional `az`/`el` set camera angles in degrees. The
  automatic camera is a three-quarter view from about `az=30`, so it hides the far side. Back:
  `view(model, "back.png", az=210, el=25)`
- `check(model)` validates attachment fits, collisions, inventory, and rigid-body stability, and prints the part count.
  Failures locate the relevant placements. Each of the first twelve collisions gives how far the two parts' bounds
  overlap and how far an attached part reaches past the hole it hangs by; the rest are counted by part pair.
  `clashes(model)` lists every colliding pair. `stability=False` is for unfinished subassemblies.
- `model.write("model.mpd")` preserves shared/nested definitions; `check=True` validates at export.
  `.ldr` flattens; `parts(model)` gives flat records with `p.stem` and resolved `p.color` (an integer LDraw code).
  `color(name)` converts a color name to that code.
```python
scene = Assembly("scene")
brick = scene.add("3001", color="red")
scene.attach("3005", to=brick.stud[0], by=("hole", 0), color="yellow")
scene.add("3005", pose=at(x=1.5, y=-2, z=0.5), color="blue")  # one plate too low, so it sinks into the red brick
print(len(parts(scene)))  # 3
print(parts(scene)[1].stem, parts(scene)[1].color, parts(scene)[1].pos)  # 3005 14 (-30.0, -24.0, -10.0)
print(clashes(scene))  # ((0, 2),): leaf indices of each colliding pair
print(len(connections(scene).components))  # 2: the seated brick joins the red one, the sunk one does not
print(color("dark bluish gray"))  # 72
```

Simulation treats each connected component as rigid; separate components may support each other. Touching is not
attaching: a part placed with `add` joins the part below only when its studs sit fully inside the holes, so the upper
underside lies on the lower top face, 4 LDU below the stud tips that `measure` reports. Placed on the tips instead, it
is a separate body and falls those 4 LDU. `attach` does this arithmetic, so use it wherever a stud is there to attach
to; `connections(model).components` lists the rigid bodies. Ground is at the lowest geometry point, so a part that dips
below everything else sets it. The displacement limit is 3 LDU. A failure names each component that moved and what it
started above or rested on. The scene has one ground, so every free-standing object must reach the same lowest
y as the object that sets it, or it starts in the air and falls. A tile placed on the ground beside a figure:
```python
scene = Assembly("scene")
scene.add(johnny_thunder())
floor = measure(scene)[1][1]  # the lowest point so far, since +Y is down
scene.add("3068b", pose=move(100, floor - measure("3068b")[1][1], 0), color="white")
check(scene)
```

## Judging
Experienced LEGO designers will judge your entry against others under the standards of a published set: is it not only
true to the prompt but well-designed? Design is form, proportion, surface, and parts chosen for the shapes they make.

## Requirements
{% if split == "model" %}
- Use **at most 400 pieces** in the final scene.
{% elif split == "val" %}
- Use **at most 100 pieces** in the final scene.
{% elif split == "set" %}
- Use **400–4000 pieces, inclusive**, in the final scene.
{% elif split == "alt-build" %}
- Use only the supplied parts and colors, up to the specified quantity of each combination. Not all pieces must be
  used. `from brickagent import catalog` gives access to `catalog.stock()`: `{stem: {color_name: count}}`, with
  `None` for unlimited counts. `catalog.stock()[stem]` gives a part's available colors and counts.

  `check(model)` enforces quantities across all occurrences, including repeated subassemblies and inherited colors.
{% endif %}
- Make all requested objects, details, poses, and spatial relationships visibly recognizable.
- The final model must pass `check(model)` with default settings: exact authored joins,
  collision checking and rigid-body stability. Multiple objects are allowed and often necessary to satisfy the prompt.
  The official validation will be performed externally.
- Render and inspect the complete model as those judges would, then refine it as needed. Passing the physical checks
  alone is insufficient.

Deliver `build.py` and `model.mpd`. `build()` should return the model. Put export under `if __name__ == "__main__":` so
the build can be imported and validated. Submissions that violate the above requirements will be given a score of 0.
