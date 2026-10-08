"""Reflection using native part counterparts."""

from graphlib import TopologicalSorter

import numpy as np

from . import catalog
from .model import Assembly, Occurrence, _definitions
from .pose import Pose, _unit

LOCAL = np.diag((-1.0, 1.0, 1.0, 1.0))


def counterpart(stem: str) -> tuple[str, np.ndarray]:
    """Counterpart stem and reflection mapping its geometry into the source part."""
    try:
        other, matrix = catalog._load("mirrors.json.xz")["parts"][stem]
    except KeyError:
        raise ValueError(f"No verified mirror geometry for {stem} ({catalog.name(stem)})") from None
    return catalog.resolve(other), np.array(matrix)


def mirror(source: Assembly | Occurrence, axis, about, name: str | None) -> Assembly | Occurrence:
    normal = _unit(np.eye(3)["xyz".index(axis)] if isinstance(axis, str) and axis in ("x", "y", "z") else axis)
    point = np.asarray(about, float)
    if normal is None or normal.shape != (3,) or point.shape != (3,) or not np.isfinite((normal, point)).all():
        raise ValueError("mirror needs x/y/z or a nonzero plane normal, and a finite point in LDU")
    plane = np.eye(4)
    plane[:3, :3], plane[:3, 3] = np.eye(3) - 2 * np.outer(normal, normal), 2 * normal * (normal @ point)
    memo = {}

    def occurrence(old: Occurrence, reflection: np.ndarray) -> tuple[str | Assembly, Pose]:
        if old.what not in memo:
            memo[old.what] = counterpart(old.what)
        what, correction = memo[old.what]
        matrix = reflection @ old.pose.matrix @ correction
        return what, Pose(tuple(matrix[:3, :3].ravel()), tuple(matrix[:3, 3]))

    # Complete children first so copies need no cycle checks.
    root = source if isinstance(source, Assembly) else source.what
    dependencies = _definitions(root) if isinstance(root, Assembly) else {}
    for original in TopologicalSorter(dependencies).static_order():
        result = Assembly(name if original is root and name is not None else original.name + " mirrored")
        for old in original._children:
            what, pose = occurrence(old, plane if original is source else LOCAL)
            result._children.append(Occurrence(result.name, what, pose, old.color, old.index))
        result._subassemblies = {memo[child][0] for child in original._subassemblies}
        # Tokens avoid retaining source assemblies.
        result._mirrored = {old._key: new for old, new in zip(original._children, result._children)}
        result._attachments = [tuple(result.connector(c) for c in pair) for pair in original._attachments]
        memo[original] = result, LOCAL

    if isinstance(source, Assembly):
        return memo[source][0]
    what, pose = occurrence(source, plane)
    return Occurrence(None, what, pose, source.color)
