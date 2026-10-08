"""Check a submission zip before uploading: python scripts/check_submission.py submission.zip

Standard library only. The zip holds one folder per task, named by task id (model-001/, set-001/, alt-001/, ...),
optionally inside a single top-level folder. Each task folder holds build.py, model.mpd, or both, plus any modules
build.py imports. Exits 1 if the zip would be rejected.
"""

import argparse
import ast
from collections import Counter
import json
from pathlib import Path, PurePosixPath
import zipfile

LIMIT = 10 * 1024 * 1024  # The form's upload limit.
SPLIT = {"model": "model", "set": "set", "alt": "alt-build"}
BENCHMARK = Path(__file__).resolve().parent.parent / "benchmark"


def task_ids() -> set[str]:
    """Every benchmark task id."""
    return {
        task["id"] for split in SPLIT.values() for task in json.loads((BENCHMARK / split / "tasks.json").read_text())
    }


def layout(names: list[str]) -> dict[str, dict[str, str]]:
    """Archive member per file, grouped by task folder, with a single shared top-level folder stripped."""
    paths = [PurePosixPath(n) for n in names if not n.endswith("/") and "__MACOSX" not in n]
    strip = int(len({p.parts[0] for p in paths}) == 1 and all(len(p.parts) > 2 for p in paths))
    files: dict[str, dict[str, str]] = {}
    for p in paths:
        parts = p.parts[strip:]
        folder, name = (parts[0], "/".join(parts[1:])) if len(parts) > 1 else ("", parts[0])
        files.setdefault(folder, {})[name] = str(p)
    return files


def defines_build(source: bytes) -> bool:
    """Whether the module parses and defines a top-level build()."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == "build" for node in tree.body)


def has_parts(data: bytes) -> bool:
    """Whether the MPD places at least one part (an LDraw type-1 line)."""
    return any(line.lstrip().startswith(b"1 ") for line in data.splitlines())


def problems(archive: zipfile.ZipFile, files: dict[str, dict[str, str]], known: set[str]) -> list[str]:
    """Every reason the zip would be rejected."""
    stray = [f"file outside a task folder: {name}" for name in sorted(files.get("", {}))]
    unknown = [f"unknown task folder: {task}/" for task in sorted(set(files) - known - {""})]
    tasks = sorted(set(files) & known)
    empty = [f"{t}/ has neither build.py nor model.mpd" for t in tasks if not {"build.py", "model.mpd"} & set(files[t])]
    scripts = [
        f"{t}/build.py does not parse or define build()"
        for t in tasks
        if "build.py" in files[t] and not defines_build(archive.read(files[t]["build.py"]))
    ]
    models = [
        f"{t}/model.mpd places no parts"
        for t in tasks
        if "model.mpd" in files[t] and not has_parts(archive.read(files[t]["model.mpd"]))
    ]
    return stray + unknown + empty + scripts + models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()

    size = args.zip.stat().st_size
    with zipfile.ZipFile(args.zip) as archive:
        known = task_ids()
        files = layout(archive.namelist())
        too_big = [f"zip is {size / 1e6:.1f} MB; the limit is 10 MB"] if size > LIMIT else []
        errors = too_big + problems(archive, files, known)
    tasks = sorted(set(files) & known)
    settings = sorted({SPLIT[t.split("-")[0]] for t in tasks})
    missing = sorted(t for t in known if SPLIT[t.split("-")[0]] in settings and t not in files)
    kinds = Counter("build.py" if "build.py" in files[t] else "model.mpd only" for t in tasks)
    print(f"{size / 1e6:.2f} MB, {len(tasks)} tasks in {', '.join(settings) or 'no setting'}: {dict(kinds)}")
    if missing:
        more = " ..." if len(missing) > 10 else ""
        print(f"{len(missing)} tasks missing (counted invalid): {', '.join(missing[:10])}{more}")
    for error in errors:
        print(f"error: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
