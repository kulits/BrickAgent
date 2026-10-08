"""Native BrickNet connector references and pairwise placement."""

from dataclasses import dataclass
from functools import cached_property, lru_cache
from itertools import groupby

import numpy as np

from bricknet.core import _AXLE_MATES, _STUD_MATES
from bricknet.data import load_catalog
from bricknet.graph import Kind, PartInfo, _AXLE_MIN_OVERLAP, _part_info
from bricknet.tree import RX_PI

from . import catalog
from .pose import Pose, spin

TYPES = tuple("stud open hole tube post axle cross pin socket bar clip hinge ball fixed".split())


class NoMate(ValueError):
    """Ambiguous, incompatible, missing or inexact connection."""


def _info(stem: str) -> PartInfo:
    """BrickNet's native connector records."""
    return _part_info(load_catalog().stem_to_id[stem])


@lru_cache(maxsize=None)
def _addresses(stem: str) -> tuple[tuple[str, int], ...]:
    """(type, typed index): the subtype for stud, hole, and axle connectors, the kind otherwise."""
    types = [sub if kind in ("stud", "hole", "axle") else kind for kind, sub, *_ in _info(stem).conns]
    return tuple((kind, i) for kind, group in groupby(types) for i, _ in enumerate(group))


@dataclass(frozen=True)
class Connector:
    """Native connector through an occurrence path. `pose` is in the enclosing frame; +Y axis, +Z roll. `length` is the
    half-extent in LDU. `index` is typed; `native_index` is BrickNet's flat index."""

    path: tuple
    native_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))

    @property
    def stem(self) -> str:
        return self.path[-1].what

    @property
    def kind(self) -> str:
        return _info(self.stem).conns[self.native_index][0]

    @property
    def sub(self) -> str:
        return _info(self.stem).conns[self.native_index][1]

    @property
    def polarity(self) -> str | None:
        return _info(self.stem).conns[self.native_index][2]

    @property
    def length(self) -> float:
        return _info(self.stem).conns[self.native_index][4]

    @property
    def type(self) -> str:
        return _addresses(self.stem)[self.native_index][0]

    @property
    def index(self) -> int:
        return _addresses(self.stem)[self.native_index][1]

    @property
    def address(self) -> str:
        return f"{self.type}[{self.index}]"

    @property
    def location(self) -> tuple[str, ...]:
        return tuple(o.location for o in self.path)

    @cached_property
    def pose(self) -> Pose:
        m = _info(self.stem).frames[self.native_index]
        frame = Pose(tuple(m[:3, :3].ravel()), tuple(m[:3, 3]))
        for occurrence in reversed(self.path):
            frame = occurrence.pose @ frame
        return frame

    @property
    def at(self) -> tuple[float, float, float]:
        return self.pose.t

    @property
    def axis(self) -> tuple[float, ...]:
        return self.pose.rot[1::3]


def mates(type: str) -> str:
    """What an address type fits, from BrickNet's pairing tables."""
    partners = set()
    for a, b in (*_STUD_MATES, *_AXLE_MATES):
        if type == a:
            partners.add(b)
        elif type == b:
            partners.add(a)
    return ", ".join(sorted(partners)) or "the other polarity"


def compatible(a: Connector, b: Connector) -> bool:
    """Whether BrickNet permits this type pairing."""
    if {a.kind, b.kind} == {"stud", "hole"}:
        return tuple(sorted((a.sub, b.sub))) in _STUD_MATES
    if a.kind == b.kind == "axle":
        return tuple(sorted((a.sub, b.sub))) in _AXLE_MATES
    return (
        a.kind == b.kind
        and a.kind in ("hinge", "ball", "fixed")
        and a.sub == b.sub
        and {a.polarity, b.polarity} == {"in", "on"}
    )


@lru_cache(maxsize=None)
def _socket_faces(stem: str, index: int) -> frozenset[int]:
    """Socket ends that also carry a native stud-receiving hole."""
    info = _info(stem)
    frame, length = info.frames[index], info.conns[index][4]
    delta = info.frames[:, :3, 3] - frame[:3, 3]
    along = delta @ frame[:3, 1]
    coaxial = np.linalg.norm(delta - along[:, None] * frame[:3, 1], axis=1) < 1e-8
    ends = (np.abs(np.abs(along) - length) < 1e-8) & (info.meta[:, 0] == Kind.hole)
    return frozenset(map(int, np.sign(along[coaxial & ends])))


def placement(
    target: Connector,
    source: Connector,
    *,
    flip: bool | None = None,
    slide: float = 0,
    yaw: float = 0,
    seat: str = "auto",
    align=None,
    orient: Pose | None = None,
) -> Pose:
    """Map source into target. Auto seats known pin collars in socket recesses; other joints align centers.
    Slide is in LDU along the target axis; yaw is right-handed about it. Flip opposes the axes; omitted, it is chosen.
    Align pairs source/target directions, including the flip; orient supplies the complete rotation."""
    a, b = target, source
    if not compatible(a, b):
        takes = "; ".join(f"{t} takes {mates(t)}" for t in dict.fromkeys((a.type, b.type)))
        raise NoMate(f"Incompatible: {a.stem}.{a.address} and {b.stem}.{b.address}; {takes}")
    offset, angle = float(slide), yaw
    if np.ndim(angle) or not np.isfinite((angle, offset)).all():
        raise ValueError("yaw and slide must be finite scalars; use orient for arbitrary rotations")
    if flip not in (None, False, True):
        raise ValueError("flip must be None, True, or False")
    side = None if flip is None else -1 if flip else 1
    if seat not in ("auto", "center", "+end", "-end"):
        raise ValueError("seat must be 'auto', 'center', '+end', or '-end'")
    if orient is not None and (align is not None or angle != 0):
        raise ValueError("Choose orient or align/yaw")
    if align is not None:
        directions = np.asarray(align, dtype=float)
        if directions.shape != (2, 3) or not np.isfinite(directions).all():
            raise ValueError("align needs two finite directions")
        lengths = np.linalg.norm(directions, axis=1)
        if min(lengths) < 1e-12:
            raise ValueError("align directions must be nonzero")
        u, v = directions / lengths[:, None]
    limit = a.length + b.length - min(_AXLE_MIN_OVERLAP, 2 * a.length, 2 * b.length)
    slide = offset
    if side is None and a.kind in ("axle", "hinge"):
        if isinstance(orient, Pose):
            side = 1 if np.dot(a.axis, orient.dir(b.axis)) >= 0 else -1
        elif align is not None:
            source_along, target_along = np.dot(u, b.axis), np.dot(v, a.axis)
            if abs(source_along) > 1e-8 and abs(target_along) > 1e-8:
                side = 1 if source_along * target_along > 0 else -1
    if seat == "auto" and a.kind == b.kind == "axle" and {a.sub, b.sub} == {"pin", "socket"}:
        pin, socket = (a, b) if a.sub == "pin" else (b, a)
        seating = catalog._load("seating.json.xz")
        direction, collar = seating["pin"][f"{pin.stem}:{pin.native_index}"]
        if direction is not None:
            faces = _socket_faces(socket.stem, socket.native_index)
            face = (next(iter(faces)) if len(faces) == 1 else 1) if side is None else side * direction
            depth = seating["socket"][f"{socket.stem}:{socket.native_index}"][(face + 1) // 2]
            start = pin.length + (depth if collar and depth is not None else 0)
            side = face * direction
            slide += face * (socket.length - start) if pin is b else direction * (start - socket.length)
    elif seat in ("+end", "-end"):
        if a.kind != "axle":
            raise ValueError("An endpoint seat requires an axle joint")
        slide += (1 if seat == "+end" else -1) * (a.length - b.length)
    side = 1 if side is None else side
    ap, bp = a.pose, b.pose
    if a.kind != "axle" and (offset or (side == -1 and a.kind != "hinge")):
        raise ValueError("slide requires an axle joint; flip requires an axle joint or hinge")
    if a.kind == "axle" and abs(slide) > limit + 1e-8:
        shift = slide - offset  # What the seat has already moved it.
        raise NoMate(f"slide {offset:g} would disengage; here it can be {-limit - shift:g} to {limit - shift:g}")
    r = RX_PI[:3, :3] if side == -1 else np.eye(3)
    if align is not None:
        u, v = r @ bp._r.T @ u, ap._r.T @ v
        radii = np.linalg.norm((u[::2], v[::2]), axis=1)
        if abs(u[1] - v[1]) > 1e-8 or abs(radii[0] - radii[1]) > 1e-8:
            raise NoMate("These directions cannot align by rotation about the joint axis")
        if max(radii) >= 1e-8:
            angle += float(np.degrees(np.arctan2(np.cross(u, v)[1], u[::2] @ v[::2])))
    if orient is not None:
        if not isinstance(orient, Pose):
            raise ValueError("orient must be a Pose")
        rotation = ap._r.T @ orient._r @ bp._r @ r.T
    else:
        rotation = spin((0, 1, 0), angle)._r
    if a.kind == "fixed":
        if not np.allclose(rotation, np.eye(3), atol=1e-8, rtol=0):
            raise ValueError("This fixed connector requires its labeled orientation; it has no rotational freedom")
    elif a.kind != "ball" and not np.allclose(rotation[:, 1], (0, 1, 0), atol=1e-8, rtol=0):
        raise NoMate("The requested orientation would tilt the joint axis; only a ball joint tilts, the rest turn")
    return ap @ Pose(tuple((rotation @ r).ravel()), (0.0, float(slide), 0.0)) @ bp.inv
