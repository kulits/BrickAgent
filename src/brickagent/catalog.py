"""Names, colors, and bounds for LDraw parts, optionally restricted through BRICKAGENT_SET."""

import json
import lzma
import os
import re
from functools import lru_cache
from importlib.resources import files
from numbers import Integral
from pathlib import Path

from bricknet.data import load_aliases, load_catalog

_DATA = files(__package__) / "_data" / os.environ.get("BRICKNET_CATALOG", "v1")


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    """Load a packaged compressed JSON table."""
    return json.loads(lzma.decompress((_DATA / name).read_bytes()))


def stock() -> dict[str, dict[str, int | None]] | None:
    """Supported inventory, or None without a restriction. None means quantities are unlimited."""
    keep, keys = _catalog(os.environ.get("BRICKAGENT_SET"))
    return None if keep is None else {stem: dict(colors) for stem, colors in keep.items() if stem in keys}


def _stem(what: str) -> str:
    """Normalize a part ID or alias; leave unknown text for name resolution."""
    key = what.strip().lower().removesuffix(".dat")
    key = load_aliases().get(key, (key, None))[0]
    return _load("ids.json.xz").get(key, key)


_DIM = r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)"


def _words(text: str) -> tuple[str, ...]:
    """Normalize title words and footprint order."""
    return tuple(re.sub(_DIM, lambda m: "x".join(sorted(m.groups(), key=float)), text.lower()).split())


def _keys() -> dict[str, tuple[str, ...]]:
    """Supported stems and title words in the active catalog."""
    return _catalog(os.environ.get("BRICKAGENT_SET"))[1]


@lru_cache(maxsize=1)
def _catalog(path: str | None) -> tuple[dict[str, dict[str, int | None]] | None, dict[str, tuple[str, ...]]]:
    """Inventory and supported name index for `path`."""
    _resolve_name.cache_clear()
    cat = load_catalog()
    keep = json.loads(Path(path).read_text()) if path else None
    return keep, {
        s: _words(cat.id_to_name[cat.stem_to_id[s]]) for s in _load("bounds.json.xz") if keep is None or s in keep
    }


def name(stem: str) -> str:
    """BrickNet's title for a supported part."""
    cat = load_catalog()
    return cat.id_to_name[cat.stem_to_id[resolve(stem)]]


def resolve(what: str) -> str:
    """Resolve a part ID (optional .dat), alias or family-first title; missing/ambiguous names raise KeyError."""
    if not isinstance(what, str):
        raise TypeError("A part must be a string name or LDraw part number")
    text = what.strip().lower()
    if not text:
        raise KeyError("A part name or number must not be empty")
    stem, keys = _stem(text), _keys()
    if stem in keys:
        return stem
    if stem in _load("bounds.json.xz"):
        raise KeyError(f"{what!r} is not in the set")
    return _resolve_name(text)


@lru_cache(maxsize=None)
def _resolve_name(text: str) -> str:
    """Resolve a normalized title within the active catalog."""
    want, keys = _words(text), _keys()
    words = set(want)
    # Prefer exact word order, then the fewest additional words.
    ranks = {s: (k != want, len(set(k))) for s, k in keys.items() if k[0] == want[0] and words.issubset(k)}
    best = min(ranks.values(), default=(True, 0))
    found = sorted(s for s in ranks if ranks[s] == best)
    if len(found) == 1:
        return found[0]
    if found:
        raise KeyError(f"{text!r} is {len(found)} parts: {[(s, name(s)) for s in found[:8]]}")
    raise KeyError(f"no part named {text!r}; find({text!r}) ranks the closest")


def measure(stem: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """LDraw surface bounds (lo, hi) in part-local LDU, including studs."""
    lo, hi = _load("bounds.json.xz")[resolve(stem)]
    return tuple(map(float, lo)), tuple(map(float, hi))


@lru_cache(maxsize=1)
def _colors() -> dict[str, int]:
    return {n.replace(" ", ""): code for n, code in load_catalog().color_to_code.items()}


def color(name: str | int) -> int:
    """LDraw color-code resolution."""
    if isinstance(name, Integral) and not isinstance(name, bool):
        return int(name)
    if not isinstance(name, str):
        raise ValueError("color must be a color name or an integer LDraw code")
    got = _colors().get(name.replace(" ", "").replace("_", "").replace("-", "").lower())
    if got is None:
        words = set(name.lower().replace("-", " ").split())
        near = sorted((n for n in load_catalog().color_to_code if words & set(n.split())), key=len)[:8]
        raise KeyError(f"no color named {name!r}; sharing a word: {', '.join(near) or 'none'}")
    return got
