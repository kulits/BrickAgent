"""Validate a submission: python scripts/verify.py WORKSPACE --split model

WORKSPACE holds the agent's build.py and model.mpd, both required. build() is rebuilt from the source and judged;
whether the rebuild reproduces model.mpd is recorded, not judged. Writes WORKSPACE/verification.json and exits nonzero
for an invalid submission.
"""

import argparse
from collections import Counter
from contextlib import redirect_stdout
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

LIMITS = {"model": (1, 400), "val": (1, 100), "set": (400, 4000), "alt-build": (1, None)}


def geometry(data: bytes) -> dict[bytes, list[list[bytes]]]:
    """Each embedded file's part lines, sorted: the same placements listed in another order are the same model."""
    files: dict[bytes, list[list[bytes]]] = {}
    name = b""
    for line in data.splitlines():
        if line.startswith(b"0 FILE "):
            name = line[7:].strip()
            files.setdefault(name, [])
        elif line.startswith(b"1 "):
            files.setdefault(name, []).append(line.split())
    return {name: sorted(lines) for name, lines in files.items()}


def rebuild(work: Path):
    """The Assembly returned by work/build.py's build(), imported with the workspace first on the path."""
    sys.path.insert(0, str(work))
    spec = importlib.util.spec_from_file_location("build", work / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build()


def verify(work: Path, split: str) -> dict:
    """Validation report for one workspace; `valid` is the verdict and `errors` the reasons."""
    import brickagent as b
    from bricknet import collision

    started, errors = time.perf_counter(), []
    report: dict = {"split": split, "collision": {"tau_ldu": collision.TAU}}
    try:
        if collision.TAU != 1.0:
            raise ValueError("Evaluation requires TAU=1.0 LDU")
        inventory = os.environ.get("BRICKAGENT_SET")
        if split == "alt-build" and not inventory:
            raise ValueError("Alt-Build is validated against its inventory; set BRICKAGENT_SET")
        inventory_bytes = Path(inventory).read_bytes() if inventory else None
        report["build_sha256"] = hashlib.sha256((work / "build.py").read_bytes()).hexdigest()
        os.chdir(work)
        with redirect_stdout(sys.stderr):  # Keep stdout for the report.
            model = rebuild(work)
        if os.environ.get("BRICKAGENT_SET") != inventory:
            raise ValueError("The build changed the inventory setting")
        if inventory and Path(inventory).read_bytes() != inventory_bytes:
            raise ValueError("The build changed the inventory file")
        flat = b.parts(model)
        report.update(
            parts=len(flat),
            distinct_parts=len({p.stem for p in flat}),
            authored_attachments=len(tuple(b.attachments(model))),
            part_color_counts=[
                [stem, color, count]
                for (stem, color), count in sorted(Counter((p.stem, p.color) for p in flat).items())
            ],
        )
        minimum, maximum = LIMITS[split]
        if len(flat) < minimum or (maximum is not None and len(flat) > maximum):
            errors.append(f"{len(flat)} parts outside {split} limits")
        expected = (work / "model.mpd").read_bytes()
        with TemporaryDirectory() as folder:
            actual = b.write(model, Path(folder) / "model.mpd").read_bytes()
        report.update(
            mpd_sha256=hashlib.sha256(expected).hexdigest(),
            mpd_geometry_reproduced=geometry(actual) == geometry(expected),
            mpd_reproduced_byte_for_byte=actual == expected,
        )
        try:
            with redirect_stdout(sys.stderr):
                b.check(model)
            report["full_default_check"] = "passed"
        except Exception as exc:
            report["full_default_check"] = "failed"
            errors.append(f"{type(exc).__name__}: {exc}")
        graph = b.connections(model)
        report.update(
            connected_components=len(graph.components),
            component_sizes=sorted(map(len, graph.components), reverse=True),
            native_connection_edges=len(graph.edges),
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return dict(report, valid=not errors, errors=errors, seconds=time.perf_counter() - started)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--split", choices=tuple(LIMITS), default="model")
    parser.add_argument("-o", "--output", type=Path, help="report path (default: WORKSPACE/verification.json)")
    args = parser.parse_args()
    work = args.workspace.resolve()
    output = (args.output or work / "verification.json").resolve()
    if os.environ.get("BRICKAGENT_SET"):  # verify() changes into the workspace.
        os.environ["BRICKAGENT_SET"] = str(Path(os.environ["BRICKAGENT_SET"]).resolve())
    report = verify(work, args.split)
    text = json.dumps(report, indent=2) + "\n"
    output.write_text(text)
    print(text, end="")
    return int(not report["valid"])


if __name__ == "__main__":
    raise SystemExit(main())
