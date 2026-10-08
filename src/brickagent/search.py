"""Part discovery using catalog names and keywords."""

from collections import Counter
from functools import cache
from itertools import groupby
import re

import numpy as np

from bricknet.data import load_aliases

from . import catalog
from .model import connectors


def _tokens(text: str) -> Counter[str]:
    text = re.sub(r"(?<=\d)\s*x\s*(?=\d)", " ", text.lower())
    return Counter(word.strip("./") for word in re.findall(r"[a-z0-9./]+", text) if word.strip("./"))


@cache
def _base(stem: str) -> str | None:
    """Undecorated counterpart inferred from LDraw numbering, for search only."""
    for i in range(len(stem) - 1, 0, -1):
        if suffix := re.fullmatch(r"[pd][a-z0-9]{1,4}(c\d\d)?", stem[i:]):
            number = stem[:i] + (suffix[1] or "")
            number = load_aliases().get(number, (number,))[0]
            if number in catalog._load("bounds.json.xz"):
                return number
    return None


@cache
def _words(stem: str) -> Counter[str]:
    return _tokens(catalog.name(stem)) | Counter(
        {stem, _base(stem) or stem, *catalog._load("keywords.json.xz").get(stem, ())}
    )


def find(query: str = "") -> tuple[str, ...]:
    """Ranked part-number strings from the active catalog; filter and slice in Python."""
    try:
        named: str | None = catalog.resolve(query)
    except KeyError:
        named = None
    asked = _tokens(query)
    matches = {stem: asked & _words(stem) for stem in catalog._keys()}
    found = [
        stem
        for stem, words in matches.items()
        if not asked or stem == named or words.keys() - matches.get(_base(stem) or "", {}).keys()
    ]
    return tuple(sorted(found, key=lambda stem: (stem != named, -matches[stem].total(), len(catalog.name(stem)), stem)))


def _grid(group: list) -> str | None:
    """Connectors filling a product of axis values, as the grid size and each axis's values; None otherwise."""
    at = np.array([c.at for c in group])
    ax = [np.unique(at[:, k]) for k in range(3)]
    if len(group) < 3 or len(np.unique(at, axis=0)) != len(group) or np.prod([len(u) for u in ax]) != len(group):
        return None
    ok = [len(u) > 2 and len(set(np.diff(u).round(6))) == 1 for u in ax]  # Evenly spaced, so a range reads well.
    sp = [f"{u[0]:g}..{u[-1]:g} step {u[1] - u[0]:g}" if e else ", ".join(f"{v:g}" for v in u) for u, e in zip(ax, ok)]
    size = " x ".join(str(len(u)) for u in ax if len(u) > 1)
    return " | ".join([f"{size} {'grid' if ' x ' in size else 'in a row'}", *(f"{a}={v}" for a, v in zip("xyz", sp))])


def _frame(c) -> tuple[str, str]:
    """The kind and subtype, and the axis, roll, and length, as describe prints them."""
    kind = f"{c.kind} sub={c.sub}" + (f" polarity={c.polarity}" if c.polarity else "")
    axis, roll = (tuple(round(v, 9) + 0.0 for v in values) for values in (c.pose.rot[1::3], c.pose.rot[2::3]))
    return kind, f"axis={axis} | roll={roll}" + (f" | half-length={c.length}" if c.kind == "axle" else "")


def describe(stem: str, type: str | None = None) -> str:
    """Connector addresses and frames; axial lengths are half-extents in LDU. A regular grid prints as one line."""
    stem = catalog.resolve(stem)
    lines = [f"{stem}: {catalog.name(stem)}", f"bounds: {catalog.measure(stem)} LDU"]
    if (stock := catalog.stock()) is not None:
        lines.append(f"inventory: {stock[stem]} (None = unlimited)")
    lines.append("connectors: at/half-length in LDU; axis=frame +Y; roll=frame +Z (part-local)")
    for (kind, rest), run in groupby(connectors(stem, type), key=_frame):
        group = list(run)
        if grid := _grid(group):
            lines.append(f"{group[0].type}[{group[0].index}..{group[-1].index}] {kind} | {grid} | {rest}")
        else:
            lines.extend(f"{c.address} {kind} | at={tuple(round(v, 9) + 0.0 for v in c.at)} | {rest}" for c in group)
    return "\n".join(lines)
