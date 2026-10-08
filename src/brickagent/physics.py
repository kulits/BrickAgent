"""Gravity testing with one rigid collider compound per connected component."""

from collections.abc import Sequence
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pybullet as pb
from scipy.spatial import ConvexHull, HalfspaceIntersection, QhullError, cKDTree
from scipy.spatial.transform import Rotation
from bricknet.collision import _MARGIN_BIAS, _path
from bricknet.data import load_catalog

from .model import Placed

LDU = 0.0004  # meters; simulation coordinates remain native LDU.
DENSITY = 1000 * LDU**3  # kg per cubic LDU, identical for every collider.
SHRINK = 0.02  # LDU inside every surface, so exactly aligned neighbors neither touch nor jam, and cores skip EPA.
STEP = 1 / 1000  # A part landing after a 4 LDU drop moves 0.44 LDU per step, well inside the tolerance.
SAMPLE = 10  # Steps between motion measurements: 100 Hz, and a tenth of the per-body client calls.
PIECES, HULL, GROUND = 1, 2, 4  # Collision filter groups; a hull meets only the ground.


def _surface(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """A convex piece's hull triangles, whatever faces the file lists; a flat piece keeps its own."""
    used = np.unique(faces)
    try:
        return used[ConvexHull(vertices[used]).simplices]
    except QhullError:
        return faces


@cache
def _mesh(stem: str) -> tuple[np.ndarray, tuple[np.ndarray, ...], ConvexHull, np.ndarray, np.ndarray]:
    """Vertices, convex faces, outer hull, mass moments and convex bounds."""
    rows = [line.split() for line in _path(load_catalog().stem_to_id[stem]).read_text().splitlines()]
    vertices, groups = [], []
    for row in rows:
        if row and row[0] == "v":
            vertices.append(row[1:4])
        elif row and row[0] == "o":
            groups.append([])
        elif row and row[0] == "f":
            # OBJ indices start at 1; negative ones count back from the latest vertex.
            indices = [int(i.split("/")[0]) for i in row[1:]]
            groups[-1].append([i - 1 if i > 0 else len(vertices) + i for i in indices])
    vertices = np.array(vertices, dtype=float)
    groups = tuple(_surface(vertices, np.array(g, dtype=int)) for g in groups)
    moments = np.zeros((4, 4))
    for faces in groups:
        tetra = np.ones((len(faces), 4, 4))
        tetra[:, 0, :3] = vertices[np.unique(faces)].mean(0)
        tetra[:, 1:, :3] = vertices[faces]
        volumes = np.abs(np.linalg.det(tetra)) / 6
        sums = tetra.sum(1)
        # Integral of [x,y,z,1] outer [x,y,z,1]: second moments, first moments, volume.
        moments += (
            np.einsum("n,nij,nik->jk", volumes, tetra, tetra) + np.einsum("n,ni,nj->ij", volumes, sums, sums)
        ) / 20
    if not np.isfinite(moments).all() or moments[3, 3] <= 0:
        raise ValueError(f"{stem}: collider has no positive volume")
    bounds = np.array([(vertices[g].min((0, 1)), vertices[g].max((0, 1))) for g in groups])
    return vertices, groups, ConvexHull(vertices), moments * DENSITY, bounds


def _contact_regions(bounds: np.ndarray, components: Sequence[Sequence[int]], pad: float) -> dict[int, np.ndarray]:
    """Nearby part bounds from other components, expanded for allowed movement."""
    labels = np.empty(len(bounds), dtype=int)
    for i, component in enumerate(components):
        labels[np.asarray(component)] = i
    contacts = {}
    centers, half = bounds.mean(1), (bounds[:, 1] - bounds[:, 0]) / 2
    radii = np.linalg.norm(half, axis=1)
    reach = radii + radii.max() + pad
    tree = cKDTree(centers)
    # Every inter-component pair has an endpoint outside the largest component.
    largest = labels[max(components, key=len)[0]]
    for i in np.flatnonzero(labels != largest):
        other = np.array(tree.query_ball_point(centers[i], reach[i]), dtype=int)
        other = other[labels[other] != labels[i]]
        other = other[np.all(np.abs(centers[other] - centers[i]) <= half[other] + half[i] + pad, axis=1)]
        for j in other:
            contacts.setdefault(i, set()).add(j)
            contacts.setdefault(j, set()).add(i)
    bounds = bounds + np.array((-pad, pad))[:, None]
    return {i: bounds[list(neighbors)] for i, neighbors in contacts.items()}


def _body(
    flat: Sequence[Placed], component: Sequence[int], offset, path: Path, client: int, regions: dict, points
) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    meshes = [_mesh(flat[i].stem) for i in component]
    transforms = np.array([flat[i].pose.matrix for i in component])
    transforms[:, :3, 3] -= offset
    moments = (transforms @ np.array([mesh[3] for mesh in meshes]) @ transforms.transpose(0, 2, 1)).sum(axis=0)
    mass = moments[3, 3]
    center = moments[:3, 3] / mass
    second = moments[:3, :3] - mass * np.outer(center, center)
    inertia = np.trace(second) * np.eye(3) - second
    diagonal, axes = np.linalg.eigh(inertia)
    axes[:, 0] *= np.sign(np.linalg.det(axes))
    start = 0
    with path.open("w") as stream:
        for i, (vertices, groups, _, _, bounds), transform in zip(component, meshes, transforms):
            if i not in regions:
                continue
            r, pos = transform[:3, :3], transform[:3, 3]
            centers = bounds.mean(1) @ r.T + pos
            half = (bounds[:, 1] - bounds[:, 0]) @ np.abs(r.T) / 2
            lo, hi = centers - half, centers + half
            touching = np.all(lo[:, None] <= regions[i][None, :, 1], axis=2) & np.all(
                hi[:, None] >= regions[i][None, :, 0], axis=2
            )
            for j, group in enumerate(g for g, keep in zip(groups, touching.any(1)) if keep):
                local = (vertices[np.unique(group)] @ r.T + pos - center) @ axes
                eq, inner = ConvexHull(local).equations, local.mean(0)  # Shrunk about the vertex centroid.
                depth = min(SHRINK, -(eq[:, :3] @ inner + eq[:, 3]).max() / 2)  # Slivers shrink by less.
                core = HalfspaceIntersection(eq + (0, 0, 0, depth), inner).intersections
                stream.writelines("v %.17g %.17g %.17g\n" % tuple(v) for v in core.tolist())
                stream.write(f"o part{i}_hull{j}\n")
                faces = ConvexHull(core, qhull_options="Qt Q12").simplices  # Q12: a nearly flat core is no error.
                stream.writelines("f %d %d %d\n" % tuple(f) for f in (faces + start + 1).tolist())
                start += len(core)
    pieces = pb.createCollisionShape(pb.GEOM_MESH, fileName=str(path), physicsClientId=client) if start else -1
    world = np.concatenate([points[i] for i in component])
    hull = ConvexHull(world).vertices
    local = (world[hull] - center) @ axes
    owners = np.repeat(component, [len(points[i]) for i in component])[hull]
    body = pb.createMultiBody(
        baseMass=float(mass),
        baseCollisionShapeIndex=pieces,
        basePosition=center,
        baseOrientation=Rotation.from_matrix(axes).as_quat(),
        linkMasses=[0.0],
        linkCollisionShapeIndices=[
            pb.createCollisionShape(pb.GEOM_MESH, vertices=local.tolist(), physicsClientId=client)
        ],
        linkVisualShapeIndices=[-1],
        linkPositions=[(0, 0, 0)],
        linkOrientations=[(0, 0, 0, 1)],
        linkInertialFramePositions=[(0, 0, 0)],
        linkInertialFrameOrientations=[(0, 0, 0, 1)],
        linkParentIndices=[0],
        linkJointTypes=[pb.JOINT_FIXED],
        linkJointAxis=[(0, 0, 1)],
        physicsClientId=client,
    )
    pb.changeDynamics(
        body,
        -1,
        mass=float(mass),
        localInertiaDiagonal=diagonal,
        lateralFriction=10,
        frictionAnchor=1,  # Resting bodies stop creeping into their neighbors.
        linearDamping=0,
        angularDamping=0,
        physicsClientId=client,
    )
    pb.changeDynamics(body, 0, lateralFriction=10, frictionAnchor=1, physicsClientId=client)
    pb.changeDynamics(body, -1, contactProcessingThreshold=SHRINK, physicsClientId=client)  # Skips aligned neighbors.
    pb.setCollisionFilterGroupMask(body, -1, PIECES, PIECES, physicsClientId=client)
    pb.setCollisionFilterGroupMask(body, 0, HULL, GROUND, physicsClientId=client)
    return body, local, center, axes, owners


def _simulate(
    flat: Sequence[Placed], components: Sequence[Sequence[int]], *, max_displacement: float = 4.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, float]:
    """Per component: peak distance (LDU) in two seconds, leaf index, height above the ground; lowest part; floor y."""
    if not np.isfinite(max_displacement) or max_displacement < 0:
        raise ValueError("Require a finite nonnegative displacement limit")
    if not flat:
        return np.zeros(0), np.zeros(0, dtype=int), np.zeros(0), -1, 0.0
    client = pb.connect(pb.DIRECT)
    try:
        pb.setPhysicsEngineParameter(
            fixedTimeStep=STEP,
            numSubSteps=1,
            numSolverIterations=20,
            deterministicOverlappingPairs=1,
            useSplitImpulse=0,
            contactSlop=0.01,
            contactBreakingThreshold=0.005,  # Of the bounding radius; the default is meter-scale.
            contactERP=0.5,
            enableFileCaching=0,
            physicsClientId=client,
        )
        pb.setGravity(0, 9.81 / LDU, 0, physicsClientId=client)
        offset = np.mean([p.pos for p in flat], axis=0)
        points = [(m := _mesh(p.stem))[0][m[2].vertices] @ np.reshape(p.rot, (3, 3)).T + p.pos - offset for p in flat]
        bounds = np.array([(p.min(0), p.max(0)) for p in points])
        ground_y = bounds[(lowest := int(bounds[:, 1, 1].argmax())), 1, 1]
        regions = _contact_regions(bounds, components, 2 * max_displacement + _MARGIN_BIAS)
        with TemporaryDirectory(prefix="brickagent-physics-") as folder:
            bodies = {}
            for i, component in enumerate(components):
                path = Path(folder) / f"{i}.obj"
                bodies[i] = _body(flat, component, offset, path, client, regions, points)
            plane = pb.createCollisionShape(pb.GEOM_PLANE, planeNormal=(0, -1, 0), physicsClientId=client)
            ground = pb.createMultiBody(
                0, plane, basePosition=(0, ground_y, 0), useMaximalCoordinates=True, physicsClientId=client
            )
            pb.changeDynamics(ground, -1, lateralFriction=10, restitution=0, physicsClientId=client)
            pb.setCollisionFilterGroupMask(ground, -1, GROUND, HULL, physicsClientId=client)
            peaks = np.zeros(len(components))
            witnesses = np.array([component[0] for component in components], dtype=int)
            for step in range(1, round(2 / STEP) + 1):
                pb.stepSimulation(physicsClientId=client)
                for j, (body, local, center, axes, owners) in list(bodies.items()) if step % SAMPLE == 0 else ():
                    pos, quat = pb.getBasePositionAndOrientation(body, physicsClientId=client)
                    delta = local @ (Rotation.from_quat(quat).as_matrix() - axes).T + (pos - center)
                    movement = np.linalg.norm(delta, axis=1)
                    furthest = movement.argmax()
                    if not np.isfinite(movement[furthest]):
                        raise RuntimeError("Stability simulation produced nonfinite motion")
                    if movement[furthest] > peaks[j]:
                        peaks[j] = movement[furthest]
                        witnesses[j] = owners[furthest]
                    if peaks[j] > max_displacement:  # Fallen; it supports nothing and would only cost the rest.
                        pb.removeBody(bodies.pop(j)[0], physicsClientId=client)
            heights = ground_y - np.array([bounds[list(c), 1, 1].max() for c in components])
            return peaks, witnesses, heights, lowest, float(ground_y + offset[1])
    finally:
        pb.disconnect(physicsClientId=client)
