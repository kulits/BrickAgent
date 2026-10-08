"""Visual question answering for the VQA column: python scripts/vqa.py RENDERS --out vqa.jsonl

One request per build shows its eight views and asks all of the task's questions, each answered yes or no. A build's
gated score credits a question only when it and every question it depends on were answered yes; the reported VQA
score is the mean gated score over a system's builds. Ends with a table per split and pooled.
"""

import argparse
from collections import defaultdict
from pathlib import Path
import re

from PIL import Image

import vlm

VIEWS = 8

BINARY = """Say yes if the views support it and no if they do not. You must choose one of the two
for every question; there is no third option. Where the evidence is thin, decide on the balance
of what the eight views show."""

HEADER = """{intro}

It was built from this brief:
{prompt}

Answer the numbered questions below about THIS build. Use the views together -- a feature need
not be visible in every view. Judge only what you can see: do not assume a detail is present
because the brief asks for it, and do not infer hidden details from how such an object is
usually made. Keep one consistent assignment of objects to roles across all the questions.

{answer_policy}

{questions}

End your reply with exactly {count} lines, nothing after them, one per question:

<number>: <yes|no>
"""

LINE = re.compile(r"^\s*(\d+)\s*[:.)]\s*(yes|no)\b", re.I | re.M)


def text(task: dict) -> str:
    """The question sheet for one task."""
    return HEADER.format(
        intro=f"You are shown {VIEWS} views of a single LEGO build, photographed from {VIEWS} angles around it.",
        prompt=task["prompt"],
        answer_policy=BINARY,
        questions="\n".join(f"{i}. {q['question']}" for i, q in enumerate(task["questions"], 1)),
        count=len(task["questions"]),
    )


def answers(task: dict):
    """Parser for one task's reply: a verdict for every question key, or None if any is missing."""

    def parse(content: str) -> dict | None:
        got = {int(m.group(1)): m.group(2).lower() for m in LINE.finditer(content)}
        numbered = list(enumerate((q["key"] for q in task["questions"]), 1))
        return {"verdicts": {key: got[n] for n, key in numbered}} if all(n in got for n, _ in numbered) else None

    return parse


def scores(task: dict, verdicts: dict[str, str]) -> dict[str, float]:
    """Flat (fraction yes) and gated (yes along the whole dependency chain) scores for one build."""
    graph = {q["key"]: q.get("dependencies", []) for q in task["questions"]}

    def passed(key: str, seen: tuple = ()) -> bool:
        return (
            key not in seen and verdicts.get(key) == "yes" and all(passed(d, (*seen, key)) for d in graph.get(key, []))
        )

    return {
        "flat": sum(v == "yes" for v in verdicts.values()) / len(verdicts),
        "gated": sum(map(passed, graph)) / len(graph),
    }


def table(records: list[dict], tasks: dict[str, dict]) -> str:
    """Mean gated and flat score per system, for each split and pooled."""
    cells = defaultdict(list)
    for r in records:
        score = scores(tasks[r["task"]], r["verdicts"])
        for split in (r["split"], "pooled"):
            cells[split, r["system"]].append(score)
    rows = [
        f"{split:10} {system:24} {len(s):4d} {sum(x['gated'] for x in s) / len(s):.3f} "
        f"{sum(x['flat'] for x in s) / len(s):.3f}"
        for (split, system), s in sorted(cells.items())
    ]
    return "\n".join([f"{'split':10} {'system':24} {'n':>4} gated flat", *rows])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("renders", type=Path, help="RENDERS/<system>/<task>/*.png")
    parser.add_argument("--splits", nargs="+", choices=vlm.SPLITS, default=list(vlm.SPLITS))
    parser.add_argument("--model", default=vlm.MODEL)
    parser.add_argument("--revision", default=vlm.REVISION)
    parser.add_argument("--batch", type=int, default=4, help="requests per generate call")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    tasks = vlm.tasks(args.splits)
    requests = [
        vlm.Request(
            key=f"{system}|{task}",
            images=lambda frames=frames: [Image.open(f) for f in frames[:VIEWS]],
            text=text(tasks[task]),
            parse=answers(tasks[task]),
            record={"system": system, "task": task, "split": tasks[task]["split"]},
        )
        for task in tasks
        for system in vlm.systems(args.renders)
        if len(frames := vlm.views(args.renders, system, task)) >= VIEWS
    ]
    records = vlm.run(requests, lambda: vlm.Judge(args.model, args.revision), args.out, args.batch)
    print(table([r for r in records if r["task"] in tasks], tasks))


if __name__ == "__main__":
    main()
