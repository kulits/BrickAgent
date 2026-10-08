"""Pairwise judging for the Elo columns: python scripts/judge.py RENDERS --question design --out design.jsonl

Every two systems that built the same task are compared, in both presentation orders. Each build is shown as one
image: its first four views tiled in a row at 1024 px. The judge answers with one digit.
Ratings come from scripts/elo.py.
"""

import argparse
from itertools import combinations
from pathlib import Path
import re

from PIL import Image

import vlm

DESIGN = """You are shown two LEGO builds. The first {views} images are Build A, photographed from
{views} angles. The next {views} images are Build B, the same {views} angles.

Pick the build that looks better designed, judged against the build quality you would
expect from an official LEGO set. Look at how well the pieces are used, not at the
subject: what a build depicts carries no weight here, and neither does how appealing you
find it.

Both builds were rendered by the same software with identical lighting and background, so
image quality carries no information.

Reply with a single digit and nothing else: 1 if the first build is the better
answer to this question, 2 if the second is."""

ALIGN = """Two people were given the same description and each built it out of LEGO.

Description:
{prompt}

The first {views} images are Build A, photographed from {views} angles. The next {views}
images are Build B, the same {views} angles.

Pick the build that better matches the description. Work through the description point by
point: the subject, the parts it says the build should have, their arrangement, and any
colours, counts or relations it states. A build that covers more of what was asked for
wins, whatever its build quality.

Reply with a single digit and nothing else: 1 if the first build is the better
answer to this question, 2 if the second is."""

PREFACE = (
    "The first image shows every view of one build, tiled. The second image shows " "the other build the same way.\n\n"
)
QUESTIONS = {"design": DESIGN, "align": ALIGN}
VIEWS, TILE = 4, 1024
CHOICE = re.compile(r"\A\s*([12])\s*\Z")


def mosaic(frames: list[Path]) -> Image.Image:
    """The views tiled left to right, four per row, each resized to TILE."""
    sheet = Image.new("RGB", (4 * TILE, (len(frames) + 3) // 4 * TILE), "white")
    for i, frame in enumerate(frames):
        sheet.paste(Image.open(frame).convert("RGB").resize((TILE, TILE), Image.LANCZOS), (i % 4 * TILE, i // 4 * TILE))
    return sheet


def verdict(order: tuple[str, str]):
    """Parser for one presentation order: the winner, when the whole reply is the digit 1 or 2."""

    def parse(content: str) -> dict | None:
        match = CHOICE.match(content)
        return {"winner": order[int(match.group(1)) - 1]} if match else None

    return parse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("renders", type=Path, help="RENDERS/<system>/<task>/*.png")
    parser.add_argument("--question", choices=sorted(QUESTIONS), required=True)
    parser.add_argument("--splits", nargs="+", choices=vlm.SPLITS, default=list(vlm.SPLITS))
    parser.add_argument("--model", default=vlm.MODEL)
    parser.add_argument("--revision", default=vlm.REVISION)
    parser.add_argument("--batch", type=int, default=8, help="requests per generate call")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    tasks = vlm.tasks(args.splits)
    built = {
        task: [s for s in vlm.systems(args.renders) if len(vlm.views(args.renders, s, task)) >= VIEWS] for task in tasks
    }
    requests = [
        vlm.Request(
            key=f"{task}|{first}|{second}",
            images=lambda t=task, f=first, s=second: [
                mosaic(vlm.views(args.renders, system, t)[:VIEWS]) for system in (f, s)
            ],
            text=PREFACE + QUESTIONS[args.question].format(views=VIEWS, prompt=tasks[task]["prompt"]),
            parse=verdict((first, second)),
            record={
                "pair_id": f"{task}|{a}|{b}",
                "task": task,
                "split": tasks[task]["split"],
                "question": args.question,
                "first_system": first,
                "second_system": second,
            },
        )
        for task, present in built.items()
        for a, b in combinations(present, 2)
        for first, second in ((a, b), (b, a))
    ]
    vlm.run(requests, lambda: vlm.Judge(args.model, args.revision), args.out, args.batch)


if __name__ == "__main__":
    main()
