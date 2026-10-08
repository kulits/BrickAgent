"""Shared MPD and flat LDraw export."""

from pathlib import Path

from .check import check as _check
from .model import Assembly, Occurrence, Placed, _children, parts


def _line(color: int, rot: tuple[float, ...], pos: tuple[float, ...], ref: str) -> str:
    nums = [f"{v:.15f}".rstrip("0").rstrip(".") for v in (*pos, *rot)]
    return f"1 {color} {' '.join('0' if v == '-0' else v for v in nums)} {ref}"


def _flat(flat: tuple[Placed, ...], head: str) -> str:
    head = " ".join(head.splitlines())
    return "\n".join([head, *[_line(p.color, p.rot, p.pos, f"{p.stem}.dat") for p in flat]]) + "\n"


def ldraw(shape: Assembly | Occurrence, title: str = "", check: bool = False) -> str:
    """MPD text preserving shared definitions; optionally validate first."""
    if check:
        _check(shape)
    head = f"0 {title or (shape.name if isinstance(shape, Assembly) else 'model')}"
    lines, subs, names, used = ["0 FILE main.ldr", head, "0 Name: main.ldr"], [], {}, {"main"}

    def body(children) -> None:
        for child in children:
            what = child.what
            if isinstance(what, Assembly):
                if what not in names:
                    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in what.name)[:240] or "sub"
                    n, tag = 1, stem
                    while tag.casefold() in used:
                        n += 1
                        tag = f"{stem}~{n}"
                    used.add(tag.casefold())
                    names[what] = f"{tag}.ldr"
                    subs.append(what)
                ref = names[what]
            else:
                ref = f"{what}.dat"
            lines.append(_line(child.color if child.color is not None else 16, child.pose.rot, child.pose.t, ref))

    body(_children(shape))
    for definition in subs:
        lines.extend((f"0 FILE {names[definition]}", f"0 {definition.name}", f"0 Name: {names[definition]}"))
        body(definition._children)
    return "\n".join(" ".join(line.splitlines()) for line in lines) + "\n"


def write(shape: Assembly | Occurrence, path: str | Path, title: str = "", check: bool = False) -> Path:
    """Write .mpd with sharing or .ldr flattened; create parent directories. UTF-8, CRLF."""
    path = Path(path)
    if path.suffix.lower() == ".mpd":
        text = ldraw(shape, title or path.stem, check=check)
    else:
        text = _flat(_check(shape) if check else parts(shape), f"0 {title or path.stem}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    return path
