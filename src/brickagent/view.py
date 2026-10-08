"""Three.js views of LDraw geometry."""

import os
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path

from .export import write
from .model import Assembly, Occurrence
from .catalog import _DATA

VIEWER = Path(__file__).with_name("viewer")


def view(
    model: Assembly | Occurrence,
    out: str | Path = "view.png",
    *,
    az: float | None = None,
    el: float | None = None,
    width: int = 1400,
    height: int = 900,
    ldraw: str | Path | None = None,
) -> Path:
    """Render a PNG; az/el override automatic angles (degrees). ldraw defaults to BRICKAGENT_LDRAW."""
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="brickagent-view-") as folder:
        model_file = write(model, Path(folder) / "model.mpd")
        subprocess.run(
            [
                os.environ.get("BRICKAGENT_VIEWER", "node"),
                str(VIEWER / "view.mjs"),
                str(model_file),
                str(out),
                *("" if v is None else str(v) for v in (az, el, width, height)),
                str(_DATA / "colors.json"),
                str(Path(ldraw or os.environ["BRICKAGENT_LDRAW"]).resolve()),
            ],
            check=True,
        )
    return out
