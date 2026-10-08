"""Render the agent prompt: python scripts/prompt.py "A lighthouse on a rocky island." --split model"""

import argparse
from pathlib import Path

from jinja2 import Environment, StrictUndefined

SPLITS = ("model", "val", "set", "alt-build")


def render(object_prompt: str, split: str) -> str:
    """The prompt for one build request and split."""
    template = Path(__file__).with_name("prompt.md").read_text(encoding="utf-8")
    return (
        Environment(undefined=StrictUndefined, keep_trailing_newline=True)
        .from_string(template)
        .render(object_prompt=object_prompt, split=split)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("object_prompt", help="what to build")
    parser.add_argument("--split", choices=SPLITS, default="model")
    args = parser.parse_args()
    print(render(args.object_prompt, args.split), end="")


if __name__ == "__main__":
    main()
