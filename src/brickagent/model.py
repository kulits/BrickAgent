"""Shared .mpd definitions and immutable placements in native LDraw coordinates."""

from collections import Counter
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

from . import catalog, joints
from .joints import Connector, NoMate, TYPES
from .pose import ID, Pose


@dataclass(frozen=True, eq=False, slots=True)
class Occurrence:
    """Placement in its parent's frame; connector attributes use typed BrickNet ordering."""

    assembly_name: str | None = field(repr=False)
    what: "str | Assembly"
    pose: Pose = ID
    color: int | None = None
    index: int = 0
    _key: object = field(default_factory=object, init=False, repr=False)
    _connectors: dict[str | None, tuple[Connector, ...]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.color is not None:
            object.__setattr__(self, "color", catalog.color(self.color))

    @property
    def location(self) -> str:
        return f"{self.assembly_name if self.assembly_name is not None else 'part'}[{self.index}]"

    def connectors(self, type: str | None = None) -> tuple[Connector, ...]:
        if isinstance(self.what, Assembly):
            raise TypeError("Use copy.connector(reference) for a connector inside a subassembly")
        if type not in (None, *TYPES):
            raise ValueError(f"Unknown connector type {type!r}; choose from {', '.join(TYPES)}")
        if type in self._connectors:
            return self._connectors[type]
        references = _Addresses(
            Connector((self,), i)
            for i, (kind, _) in enumerate(joints._addresses(self.what))
            if type is None or kind == type
        )
        references.part, references.type = self.what, type
        if self.assembly_name is not None:  # Detached discovery must not cache references back to itself.
            self._connectors[type] = references
        return references

    def __getattr__(self, name: str) -> tuple[Connector, ...]:
        if name not in TYPES or isinstance(self.what, Assembly):
            raise AttributeError(name)
        return self.connectors(name)

    def connector(self, reference: Connector) -> Connector:
        """Bind a definition reference to this copy, including mirrored counterparts."""
        if not isinstance(self.what, Assembly):
            raise ValueError("The reference must belong to this copy's Assembly definition")
        reference = self.what.connector(reference)
        return Connector((self, *reference.path), reference.native_index)

    def mirror(self, axis="x", *, about=(0, 0, 0), name: str | None = None) -> "Occurrence":
        """Reflect in the parent's frame, returning a detached placement for add().
        axis is x/y/z or a plane normal; about is a point in LDU."""
        from .reflection import mirror

        return mirror(self, axis, about, name)


class _Addresses(tuple):
    """Connectors of one type on a part; a missing index names every connector the part has."""

    def near(self, point) -> Connector:
        """The connector of this type nearest to `point`, in the frame its `.at` is given in."""
        if not self:
            return self[0]  # Raises the message that names the part's connectors.
        return min(self, key=lambda c: sum((a - b) ** 2 for a, b in zip(c.at, point)))

    def __getitem__(self, index):
        try:
            return super().__getitem__(index)
        except IndexError:
            found = connectors(self.part)
            counts = Counter(c.type for c in found)
            addresses = ", ".join(f"{t}[0..{n - 1}]" if n > 1 else f"{t}[0]" for t, n in counts.items()) or "none"
            raise NoMate(f"{self.part}: no {self.type}[{index}]; its connectors: {addresses}") from None


def connectors(stem: str, type: str | None = None) -> tuple[Connector, ...]:
    """Part-local references with at/axis/sub fields; pass a selected reference as attach(by=...)."""
    return Occurrence(None, catalog.resolve(stem)).connectors(type)


class Assembly:
    """Mutable MPD definition with its own frame."""

    def __init__(self, name: str) -> None:
        if not isinstance(name, str):
            raise TypeError("An Assembly name must be a string")
        self.name = name
        self._children: list[Occurrence] = []
        self._attachments: list[tuple[Connector, Connector]] = []
        self._mirrored: dict[object, Occurrence] = {}
        self._subassemblies: set[Assembly] = set()

    def __repr__(self) -> str:
        return f"Assembly(name={self.name!r})"

    def __copy__(self) -> "Assembly":
        """Copy all reachable definitions, preserving sharing within the result."""
        return deepcopy(self)

    @property
    def children(self) -> tuple[Occurrence, ...]:
        """Snapshot of immediate placements; edit through add/attach."""
        return tuple(self._children)

    def write(self, path: str | Path, *, check: bool = False) -> Path:
        """Export .mpd with sharing, or .ldr flattened."""
        from .export import write

        return write(self, path, check=check)

    def mirror(self, axis="x", *, about=(0, 0, 0), name: str | None = None) -> "Assembly":
        """Independent reflection, retaining internal sharing and using native counterpart parts. `axis` is x/y/z or a
        plane normal; `about` is in LDU. Missing required connectors raise NoMate."""
        from .reflection import mirror

        return mirror(self, axis, about, name)

    def connector(self, reference: Connector) -> Connector:
        """Resolve a definition reference, mapping native counterparts when mirrored."""
        if _contains(self, reference):
            return reference
        model, path = self, []
        for old in reference.path:
            if not isinstance(model, Assembly) or old._key not in model._mirrored:
                raise ValueError("The reference must belong to this mirror's source definition")
            new = model._mirrored[old._key]
            path.append(new)
            model = new.what
        index = catalog._load("mirrors.json.xz")["connectors"][reference.stem][reference.native_index]
        if index is None:
            raise NoMate(
                f"No native mirrored connector for {'/'.join(reference.location)}.{reference.address} "
                f"({reference.stem} -> {model}): no exact, unambiguous native annotation match"
            )
        return Connector(tuple(path), index)

    def add(self, what: "str | Assembly | Occurrence", *, pose: Pose = ID, color=None) -> Occurrence:
        """Append at `pose`. Copies retain `color` and compose `pose` outside the copied pose."""
        if not isinstance(pose, Pose):
            raise TypeError("`pose` must be a Pose")
        if isinstance(what, Occurrence):
            pose = pose @ what.pose
            color = what.color if color is None else color
            what = what.what
        if isinstance(what, Assembly):
            if self in _definitions(what):
                raise ValueError("An Assembly cannot contain itself, directly or indirectly")
        else:
            what = catalog.resolve(what)
        child = Occurrence(self.name, what, pose, color, len(self._children))
        self._children.append(child)
        if isinstance(what, Assembly):
            self._subassemblies.add(what)
        return child

    def attach(
        self,
        what: "str | Assembly",
        *,
        to: Connector,
        by: Connector | tuple[str, int] | None = None,
        flip: bool | None = None,
        slide: float = 0,
        yaw: float = 0,
        color: str | int | None = None,
        seat: str = "auto",
        align=None,
        orient: Pose | None = None,
    ) -> Occurrence:
        """Append through `to`/`by`; see joints.placement for units and orientation. Omit `by` only for a unique
        compatible source. Assembly sources take definition references."""
        if not isinstance(to, Connector) or not _contains(self, to):
            raise ValueError("The target connector must belong to this assembly; bind it through the placed copy")
        if isinstance(what, Assembly):
            if not isinstance(by, Connector):
                raise ValueError("`by` for an Assembly is a connector of a part inside it, such as the one it returned")
            source = what.connector(by)
        elif isinstance(by, Connector):
            if (
                len(by.path) != 1
                or by.path[0].assembly_name is not None
                or by.path[0].pose != ID
                or by.stem != catalog.resolve(what)
                or not 0 <= by.native_index < len(joints._info(by.stem).conns)
            ):
                raise ValueError("`by` for a part is (type, index) or one from connectors(what), not a placed one")
            source = by
        elif by is not None:
            type, index = by
            source = connectors(what, type)[index]
        else:
            matches = [c for c in connectors(what) if joints.compatible(to, c)]
            if len(matches) != 1:
                addresses = ", ".join(c.address for c in matches) or "none"
                raise NoMate(f"{what}: choose `by=(type, index)`; compatible source addresses: {addresses}")
            source = matches[0]
        pose = joints.placement(to, source, flip=flip, slide=slide, yaw=yaw, seat=seat, align=align, orient=orient)
        child = self.add(what, pose=pose, color=color)
        bound = child.connector(source) if isinstance(what, Assembly) else Connector((child,), source.native_index)
        self._attachments.append((to, bound))
        return child


def _definitions(root: Assembly) -> dict[Assembly, set[Assembly]]:
    """Reachable MPD definitions and their immediate dependencies."""
    graph, pending = {}, [root]
    while pending:
        model = pending.pop()
        if model not in graph:
            graph[model] = model._subassemblies
            pending.extend(model._subassemblies)
    return graph


def _contains(shape: Assembly | Occurrence, reference: Connector) -> bool:
    """Check ownership by indexed identity."""
    if isinstance(shape, Occurrence):
        return reference.path[0] is shape
    children = shape._children
    for child in reference.path:
        if not 0 <= child.index < len(children) or children[child.index]._key is not child._key:
            return False
        children = child.what._children if isinstance(child.what, Assembly) else ()
    return True


def _children(shape: Assembly | Occurrence) -> Sequence[Occurrence]:
    if isinstance(shape, Assembly):
        return shape._children
    if isinstance(shape, Occurrence):
        return (shape,)
    raise TypeError("Expected an Assembly or Occurrence")


@dataclass(frozen=True)
class Placed:
    """Leaf snapshot with a native LDraw pose (row-major `rot`) and occurrence `path`."""

    stem: str
    color: int
    rot: tuple[float, ...]
    pos: tuple[float, float, float]
    path: tuple[str, ...]

    @property
    def pose(self) -> Pose:
        return Pose(self.rot, self.pos)


def parts(shape: Assembly | Occurrence) -> tuple[Placed, ...]:
    """Flatten to leaf snapshots in the enclosing frame."""
    out = []
    stack = [(iter(_children(shape)), ID, 16, ())]  # Explicit, so a hierarchy deeper than the recursion limit works.
    while stack:
        children, parent, color, path = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            continue
        world = parent @ child.pose
        tint = color if child.color in (None, 16) else child.color
        here = (*path, child.location)
        if isinstance(child.what, Assembly):
            stack.append((iter(child.what._children), world, tint, here))
        else:
            out.append(Placed(child.what, tint, world.rot, world.t, here))
    return tuple(out)


def measure(shape: "str | Assembly | Occurrence") -> tuple[tuple[float, ...], tuple[float, ...]]:
    """LDraw surface bounds (lo, hi) in LDU: a part in its own frame, or a model's parts in the enclosing frame."""
    if not isinstance(shape, (Assembly, Occurrence)):
        return catalog.measure(shape)
    box = [p.pose(c) for p in parts(shape) for c in product(*zip(*catalog.measure(p.stem)))]
    return tuple(map(min, zip(*box))), tuple(map(max, zip(*box)))


def attachments(shape: Assembly | Occurrence) -> tuple[tuple[Connector, Connector], ...]:
    """Authored connector pairs inside `shape`, including nested copies, bound into its enclosing frame."""
    pairs = list(shape._attachments) if isinstance(shape, Assembly) else []
    pending = [(child.what, (child,)) for child in reversed(_children(shape))]
    while pending:
        model, path = pending.pop()
        if isinstance(model, Assembly):
            pairs.extend(
                tuple(Connector((*path, *c.path), c.native_index) for c in pair) for pair in model._attachments
            )
            pending.extend((child.what, (*path, child)) for child in reversed(model._children))
    return tuple(pairs)
