"""Flatten an MPD for rendering: python scripts/flatten.py model.mpd model.ldr

Every reference to an embedded file is expanded in place, composing poses and resolving color 16 to the referring
line's color. The output holds one type-1 line per library part, in the MPD's order.
"""

import argparse
from pathlib import Path

import numpy as np


def files(text: str) -> dict[str, list[str]]:
    """Embedded files by lowercased name, the first also under ""; a plain LDR is one unnamed file."""
    found: dict[str, list[str]] = {}
    name, lead = None, []
    for line in text.splitlines():
        if line.startswith("0 FILE "):
            name = line[7:].strip().lower()
            found.setdefault(name, [])
            found.setdefault("", found[name])
        elif line.strip() != "0 NOFILE":
            (found.setdefault(name, []) if name else lead).append(line)
    return found or {"": lead}


def parts(found: dict, name: str = "", pose=np.eye(4), color: str = "16", above: tuple = ()) -> list:
    """(color, 4x4 pose, part file) for every library part under one embedded file, depth first."""
    rows = [line.split() for line in found[name]]
    return [
        placed
        for fields in rows
        if len(fields) >= 15 and fields[0] == "1"
        for placed in _expand(found, fields, name, pose, color, above)
    ]


def _expand(found: dict, fields: list[str], name: str, pose, color: str, above: tuple) -> list:
    values = np.array(fields[2:14], dtype=float)
    local = np.eye(4)
    local[:3, 3], local[:3, :3] = values[:3], values[3:].reshape(3, 3)
    child, shade = " ".join(fields[14:]), color if fields[1] == "16" else fields[1]
    if child.lower() in found and child.lower() not in above:
        return parts(found, child.lower(), pose @ local, shade, (*above, name))
    return [(shade, pose @ local, child)]


def number(value: float) -> str:
    return f"{round(value, 6) + 0.0:.6f}".rstrip("0").rstrip(".")


def flat(text: str, title: str) -> str:
    """Flat LDraw text for an MPD or LDR."""
    rows = [
        " ".join(["1", shade, *map(number, [*pose[:3, 3], *pose[:3, :3].ravel()]), part])
        for shade, pose, part in parts(files(text))
    ]
    return "\n".join([f"0 {title}", f"0 Name: {title}.ldr", *rows]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpd", type=Path)
    parser.add_argument("ldr", type=Path)
    args = parser.parse_args()
    args.ldr.parent.mkdir(parents=True, exist_ok=True)
    args.ldr.write_text(flat(args.mpd.read_text(encoding="utf-8", errors="replace"), args.ldr.stem))


if __name__ == "__main__":
    main()
