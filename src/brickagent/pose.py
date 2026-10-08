"""Rigid frames in native LDraw/BrickNet LDU: x and z horizontal, -y up."""

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation

STUD = 20
PLATE = 8
BRICK = 24
UP = (0.0, -1.0, 0.0)

_ID9 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _unit(v) -> np.ndarray | None:
    """v normalized, or None for a zero vector."""
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return None if n < 1e-12 else v / n


@dataclass(frozen=True)
class Pose:
    """Rigid frame: `rot` row-major 3x3, `t` translation. p @ q composes (q inside p), p.inv inverts, p(pt) maps a
    point, p.dir(v) rotates a direction. Rotation must be proper and orthogonal within 1e-8."""

    rot: tuple[float, ...] = _ID9  # row-major 3x3
    t: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if len(self.rot) != 9 or len(self.t) != 3 or not np.isfinite((*self.rot, *self.t)).all():
            raise ValueError("Pose needs nine finite rotation values and three finite translation values")
        object.__setattr__(self, "rot", tuple(map(float, self.rot)))
        object.__setattr__(self, "t", tuple(map(float, self.t)))
        r = self._r
        if np.max(np.abs(r.T @ r - np.eye(3))) > 1e-8 or np.linalg.det(r) <= 0:
            raise ValueError("Pose requires a proper rotation; scale, shear and reflection are not supported")

    @property
    def _r(self) -> np.ndarray:
        return np.reshape(self.rot, (3, 3))

    @property
    def matrix(self) -> np.ndarray:
        return np.block([[self._r, np.reshape(self.t, (3, 1))], [np.array([[0.0, 0.0, 0.0, 1.0]])]])

    def __matmul__(self, q: "Pose") -> "Pose":
        return Pose(tuple((self._r @ q._r).ravel()), self(q.t))

    @property
    def inv(self) -> "Pose":
        r = self._r.T
        return Pose(tuple(r.ravel()), tuple(-r @ self.t))

    def __call__(self, pt) -> tuple[float, float, float]:
        return tuple((self._r @ pt + self.t).tolist())

    def dir(self, v) -> tuple[float, float, float]:
        return tuple((self._r @ v).tolist())


ID = Pose()


def at(x: float = 0, y: float = 0, z: float = 0) -> Pose:
    """Translation by (20x, 8y, 20z) LDU: studs along x/z, plates along y; negative y is up. This is a units helper."""
    return Pose(_ID9, (STUD * float(x), PLATE * float(y), STUD * float(z)))


def move(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Pose:
    """Translation by LDU."""
    return Pose(_ID9, (float(x), float(y), float(z)))


def spin(axis, degrees: float, about=(0.0, 0.0, 0.0)) -> Pose:
    """Rotation by `degrees` (right-handed) about the line through `about` (LDU) along `axis`."""
    u = _unit(axis)
    if u is None or u.shape != (3,):
        raise ValueError("axis must be a nonzero 3-vector")
    r = Rotation.from_rotvec(u * np.radians(degrees)).as_matrix()
    if degrees % 90 == 0 and np.count_nonzero(u) == 1:
        r = np.rint(r)  # Preserve exact quarter turns.
    return Pose(tuple(r.ravel()), tuple(about - r @ about))
