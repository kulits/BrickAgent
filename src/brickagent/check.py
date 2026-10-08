"""Check collisions, connections, inventory, and stability."""

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from itertools import product
from os.path import commonprefix

import numpy as np

from bricknet.collision import colliding_pairs
from bricknet.core import Graph
from bricknet.data import load_catalog
from bricknet.graph import _edge_matrix, parse_ldr

from . import catalog
from .joints import Connector
from .model import Assembly, Occurrence, Placed, _contains, attachments, parts
from .physics import _simulate


Contacts = Iterable[tuple[Connector, Connector]]


def _bricknet_parts(flat: Sequence[Placed]) -> tuple[list[int], np.ndarray]:
    """Native part IDs and 4x4 transforms."""
    ids = [load_catalog().stem_to_id[p.stem] for p in flat]
    return ids, np.reshape([p.pose.matrix for p in flat], (-1, 4, 4))


def connections(shape: Assembly | Occurrence) -> Graph:
    """BrickNet's detected graph, including tolerated near misses; check() requires exact authored contacts."""
    from .export import _flat

    return parse_ldr(_flat(parts(shape), ""))


def clashes(shape: Assembly | Occurrence) -> tuple[tuple[int, int], ...]:
    """Colliding leaf indices using BrickNet's convex colliders and collision tolerance."""
    return tuple(colliding_pairs(*_bricknet_parts(parts(shape))))


def _contact_errors(
    shape: Assembly | Occurrence, flat: Sequence[Placed], graph: Graph, matrices: np.ndarray, contacts: Contacts
) -> Iterator[str]:
    """Missing or inexact authored contacts."""
    indices = {p.path: i for i, p in enumerate(flat)}
    edges = {frozenset(((e["a"], e["a_conn"]), (e["b"], e["b_conn"]))): e for e in graph.edges}
    for a, b in contacts:
        if not all(_contains(shape, connector) for connector in (a, b)):
            raise ValueError("Required contacts must be bound into the assembly being checked")
        edge = edges.get(frozenset((indices[c.location], c.native_index) for c in (a, b)))
        address = " to ".join(f"{'/'.join(c.location)}.{c.address}" for c in (a, b))
        if edge is None:
            yield f"Missing connection: {address}"
            continue
        i, j = edge["a"], edge["b"]
        expected = matrices[i] @ _edge_matrix(edge, graph.part_ids[i], graph.part_ids[j])
        actual = matrices[j]
        distance = np.linalg.norm(expected[:3, 3] - actual[:3, 3])
        # ||R-S||_F = sqrt(8) * sin(angle/2) for proper rotations.
        angle = 2 * np.arcsin(min(1, np.linalg.norm(expected[:3, :3] - actual[:3, :3]) / np.sqrt(8)))
        if distance > 1e-8 or angle > 1e-8:  # Roundoff only, not matching tolerance.
            yield f"Non-exact connection: {address}; residual {distance:.6g} LDU, {np.degrees(angle):.6g} deg"


def _bounds(flat: Sequence[Placed], matrices: np.ndarray) -> np.ndarray:
    """World bounds (lo, hi) of every part, from the corners of its LDraw box; +Y is down."""
    boxes = catalog._load("bounds.json.xz")  # Not measure(): a part outside the active set must reach the stock check.
    corners = np.reshape([list(product(*zip(*boxes[p.stem]))) for p in flat], (-1, 8, 3))
    world = corners @ matrices[:, :3, :3].transpose(0, 2, 1) + matrices[:, None, :3, 3]
    return np.stack((world.min(1), world.max(1)), axis=1)


def _reach(bounds: np.ndarray, hole: Connector) -> float:
    """How far a part's bounds extend past the plane of the hole it hangs by, toward whatever it is mounted on."""
    return float(((np.array(list(product(*zip(*bounds)))) - hole.at) @ hole.axis).max())


def _support(box: np.ndarray, component: Sequence[int]) -> tuple[float, int]:
    """The gap in LDU down to the nearest part of another component under this one's footprint, and that part."""
    mine = np.isin(np.arange(len(box)), component)
    part, other = box[mine][:, None], box[None]
    across = (other[..., 0, ::2] < part[..., 1, ::2] - 0.5) & (other[..., 1, ::2] > part[..., 0, ::2] + 0.5)
    below = across.all(-1) & ~mine & (other[..., 1, 1] > part[..., 1, 1])  # Under the footprint and reaching lower.
    gaps = np.where(below, other[..., 0, 1] - part[..., 1, 1], np.inf)
    return float(gaps.min()), int(gaps.min(0).argmin()) if below.any() else -1


def check(
    shape: Assembly | Occurrence,
    *,
    contacts: Contacts = (),
    stability: bool | None = None,
    max_displacement: float = 3.0,
) -> tuple[Placed, ...]:
    """Return placements or raise with localized failures. Disable stability for unfinished subassemblies."""
    flat = parts(shape)
    graph = connections(shape)
    ids, matrices = _bricknet_parts(flat)
    collisions = tuple(colliding_pairs(ids, matrices))
    authored = attachments(shape)
    issues = list(_contact_errors(shape, flat, graph, matrices, (*authored, *contacts)))
    mounts = {b.location: f"{b.address} to {'/'.join(a.location)}.{a.address}" for a, b in authored}
    hung = {b.location: b for _, b in authored}  # The connector each attached part hangs by.

    def location(i: int) -> str:
        label = f"#{i} {flat[i].stem} in {'/'.join(flat[i].path)} at {tuple(round(v, 3) for v in flat[i].pos)} LDU"
        return f"{label}; attached {mounts[flat[i].path]}" if flat[i].path in mounts else label

    box = _bounds(flat, matrices)
    for a, b in collisions[:12]:
        shared = np.minimum(box[a, 1], box[b, 1]) - np.maximum(box[a, 0], box[b, 0])
        overlap = " x ".join(f"{v:g}" for v in shared.round(3))
        line = f"collision: {location(a)} <> {location(b)}; bounds overlap {overlap} LDU"
        for i in (a, b):
            hole = hung.get(flat[i].path)
            past = round(_reach(box[i], hole), 3) if hole is not None and hole.kind == "hole" else 0
            if past > 0.5:
                line += f"; #{i} reaches {past:g} LDU past the {hole.address} it hangs by"
        issues.append(line)
    kinds = Counter(" <> ".join(sorted((flat[a].stem, flat[b].stem))) for a, b in collisions)
    by_part = ", ".join(f"{pair} x{n}" for pair, n in kinds.most_common())
    issues += [f"pairs by part: {by_part}; clashes(model) lists every pair"] * (len(collisions) > 12)
    if (stock := catalog.stock()) is not None:
        allowed = {(s, catalog.color(c)): n for s, colors in stock.items() for c, n in colors.items()}
        for (stem, color), used in Counter((p.stem, p.color) for p in flat).items():
            if (available := allowed.get((stem, color), 0)) is not None and used > available:
                issues.append(f"stock: {stem}, color {color}: used {used}, available {available}")
    if stability or (stability is None and not collisions):
        motion, witnesses, heights, lowest, floor = _simulate(flat, graph.components, max_displacement=max_displacement)
        for j in np.flatnonzero(motion > max_displacement):
            gap, below = _support(box, graph.components[j])
            start = "on the ground" if heights[j] <= 0.01 else f"{heights[j]:g} LDU above the ground, nothing beneath"
            if below >= 0 and gap > 0.5:
                start = f"{round(gap, 3):g} LDU above {location(below)}"
            elif below >= 0:
                start = f"resting on {location(below)} without a connection"
            where = "/".join(commonprefix([flat[i].path for i in graph.components[j]])) or "top level"
            head = f"unstable component {where}: moved more than {max_displacement:g} LDU in two seconds; {start}"
            issues.append(f"{head}; furthest point {location(witnesses[j])}")
        if n := int(((motion > max_displacement) & (heights > 0.01)).sum()):
            issues.insert(0, f"ground y={floor:g} is set by {location(lowest)}; unstable and starting above it: {n}")
    if issues:
        head = f"{len(flat)} parts; {len(collisions)} collision pairs; {len(graph.components)} connected components"
        head += "; stability not simulated until the collisions are resolved" * (stability is None and bool(collisions))
        raise ValueError("\n".join([head, *issues]))
    print(f"{len(flat)} parts")
    return flat
