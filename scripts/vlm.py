"""Shared pieces of the VLM evaluations: benchmark tasks, rendered views, and a resumable local Gemma 4 judge.

Renders are read from RENDERS/<system>/<task>/*.png, one directory per build, views in name order (bricknet-render
writes <stem>_0000.png to <stem>_0007.png). Tasks are read from benchmark/<split>/tasks.json.
"""

from dataclasses import dataclass
from itertools import islice
import json
from pathlib import Path
from typing import Callable, Iterable, Iterator

from PIL import Image

BENCHMARK = Path(__file__).resolve().parent.parent / "benchmark"
SPLITS = ("alt-build", "model", "set")
MODEL, REVISION = "google/gemma-4-31B-it", "842da3794eaa0b77d5f08bae87a17459d91ff475"


def tasks(splits: Iterable[str] = SPLITS) -> dict[str, dict]:
    """Benchmark tasks by id, each with its split, prompt, and question graph."""
    return {
        task["id"]: task | {"split": split}
        for split in splits
        for task in json.loads((BENCHMARK / split / "tasks.json").read_text())
    }


def views(renders: Path, system: str, task: str) -> list[Path]:
    """One build's rendered views in order; empty when the build has none."""
    return sorted((renders / system / task).glob("*.png"))


def systems(renders: Path) -> list[str]:
    return sorted(p.name for p in renders.iterdir() if p.is_dir())


@dataclass(frozen=True)
class Request:
    """One judge call: `images()` then `text` in a single user turn; `parse` turns the reply into fields or None."""

    key: str
    images: Callable[[], list[Image.Image]]
    text: str
    parse: Callable[[str], dict | None]
    record: dict


class Judge:
    """Gemma 4 from the Hugging Face Hub, decoding greedily without thinking."""

    def __init__(self, model: str = MODEL, revision: str = REVISION, max_new_tokens: int = 2048):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        self.torch, self.max_new_tokens = torch, max_new_tokens
        self.processor = AutoProcessor.from_pretrained(model, revision=revision, padding_side="left")
        self.model = AutoModelForMultimodalLM.from_pretrained(model, revision=revision, dtype="auto", device_map="auto")

    def __call__(self, batch: list[Request]) -> list[str]:
        """Replies to a batch of requests, in order."""
        conversations = [
            [
                {
                    "role": "user",
                    "content": [{"type": "image", "image": image} for image in r.images()]
                    + [{"type": "text", "text": r.text}],
                }
            ]
            for r in batch
        ]
        inputs = self.processor.apply_chat_template(
            conversations,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            add_generation_prompt=True,
            enable_thinking=False,
            processor_kwargs={"padding": True},
        ).to(self.model.device)
        with self.torch.inference_mode():
            outputs = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
        return self.processor.batch_decode(outputs[:, inputs["input_ids"].shape[-1] :], skip_special_tokens=True)


def chunks(items: list, size: int) -> Iterator[list]:
    iterator = iter(items)
    return iter(lambda: list(islice(iterator, size)), [])


def run(requests: list[Request], judge: Callable[[], Callable], out: Path, batch: int) -> list[dict]:
    """Answer requests not already recorded as ok in `out`, appending each record; returns every ok record.

    `judge()` makes the judge, called only when there is work left."""
    previous = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    done = {r["key"] for r in previous if r.get("ok")}
    todo = [r for r in requests if r.key not in done]
    print(f"{len(requests)} requests, {len(todo)} to run", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    fresh = []
    model = judge() if todo else None
    with out.open("a") as stream:
        for group in chunks(todo, batch):
            for request, reply in zip(group, model(group)):
                parsed = request.parse(reply)
                outcome = {"ok": True, **parsed} if parsed is not None else {"ok": False, "reply": reply[-200:]}
                record = {"key": request.key, **request.record, **outcome}
                stream.write(json.dumps(record) + "\n")
                fresh.append(record)
            stream.flush()
            print(f"  {len(fresh)}/{len(todo)}", flush=True)
    failed = sum(not r["ok"] for r in fresh)
    print(f"done: {len(fresh) - failed} ok, {failed} unparsed", flush=True)
    return [r for r in previous + fresh if r.get("ok")]
