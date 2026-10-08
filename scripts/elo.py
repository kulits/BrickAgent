"""Elo ratings from pairwise verdicts: python scripts/elo.py align.jsonl design.jsonl

Each question is fitted separately as a Bradley--Terry model (Hunter's MM algorithm, half a virtual tie per pair of
systems) and centered so the core systems average 1000. The combined rating rescales both to the mean of their core
dispersions and averages them, weighting prompt adherence and design equally. Intervals come from one bootstrap over
tasks shared by both questions. Systems outside --core are placed against the core's ratings without moving them.
"""

import argparse
import json
from pathlib import Path

import numpy as np

ELO = 400.0 / np.log(10.0)


def fit(n: int, i: np.ndarray, j: np.ndarray, w: np.ndarray, frozen: np.ndarray | None = None) -> np.ndarray:
    """Log-strengths; `w` is i's share of each i-vs-j win. `frozen` fixes some log-strengths (NaN = free)."""
    prior = 0.5
    fixed = np.zeros(n, bool) if frozen is None else ~np.isnan(frozen)
    free = ~fixed
    pair = (free[:, None] | free[None, :]) & ~np.eye(n, dtype=bool)
    games = np.zeros((n, n))
    np.add.at(games, (i, j), 1.0)
    games = games + games.T + 2 * prior * pair
    wins = np.bincount(i, w, n) + np.bincount(j, 1 - w, n) + prior * pair.sum(1)
    p = np.where(fixed, np.exp(np.nan_to_num(frozen if frozen is not None else np.zeros(n))), 1.0)
    for _ in range(2000):
        nxt = np.where(fixed, p, wins / (games / (p[:, None] + p[None, :])).sum(1))
        nxt = nxt if fixed.any() else nxt / np.exp(np.log(nxt).mean())
        if np.abs(np.log(nxt) - np.log(p)).max() < 1e-10:
            return np.log(nxt)
        p = nxt
    return np.log(p)


def rating(n: int, i: np.ndarray, j: np.ndarray, w: np.ndarray, core: np.ndarray) -> np.ndarray:
    """Core-only fit centered on the core, then every other system fitted against it; core mean at 1000."""
    inner = core[i] & core[j]
    first = fit(n, i[inner], j[inner], w[inner])
    s = 1000.0 + ELO * fit(n, i, j, w, frozen=np.where(core, first - first[core].mean(), np.nan))
    return s - s[core].mean() + 1000.0


def combined(align: np.ndarray, design: np.ndarray, core: np.ndarray) -> np.ndarray:
    """Equal-weight mean after rescaling each question to the mean of the two core standard deviations."""
    sd = np.array([align[core].std(ddof=1), design[core].std(ddof=1)])
    scale = np.divide(sd.mean(), sd, out=np.ones(2), where=sd > 0)  # A question without spread is left as is.
    return 1000.0 + 0.5 * sum((x - 1000.0) * k for x, k in zip((align, design), scale))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("verdicts", nargs="+", type=Path)
    parser.add_argument("--core", nargs="*", help="systems that set the scale (default: all)")
    parser.add_argument("--split", action="append", default=[], help="restrict to a split; repeatable")
    parser.add_argument("--boot", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    rows = [
        r
        for path in args.verdicts
        for r in map(json.loads, path.read_text().splitlines())
        if r.get("ok") and (not args.split or r["split"] in args.split)
    ]
    if not {r["question"] for r in rows} >= {"align", "design"}:
        raise SystemExit("Need align and design verdicts for the selected splits")
    pairs = [r["pair_id"].split("|") for r in rows]
    systems = sorted({s for _, a, b in pairs for s in (a, b)})
    n, index = len(systems), {s: k for k, s in enumerate(systems)}
    if args.core and set(args.core) - set(systems):
        raise SystemExit(f"Unknown --core systems: {', '.join(sorted(set(args.core) - set(systems)))}")
    core = np.array([args.core is None or s in args.core for s in systems])
    question = np.array([r["question"] for r in rows])
    task_names = sorted({t for t, _, _ in pairs})
    task = np.array([task_names.index(t) for t, _, _ in pairs])
    i = np.array([index[a] for _, a, _ in pairs])
    j = np.array([index[b] for _, _, b in pairs])
    w = np.array([float(r["winner"] == a) for r, (_, a, _) in zip(rows, pairs)])

    def three(rows_: np.ndarray) -> np.ndarray:
        """Align, design and combined ratings over a multiset of row indices."""
        al, de = (rating(n, i[k], j[k], w[k], core) for k in (rows_[question[rows_] == q] for q in ("align", "design")))
        return np.stack([al, de, combined(al, de, core)])

    point = three(np.arange(len(rows)))
    by_task = [np.flatnonzero(task == t) for t in range(len(task_names))]
    rng = np.random.default_rng(args.seed)
    draws = np.stack(
        [
            three(np.concatenate([by_task[k] for k in rng.integers(0, len(by_task), len(by_task))]))
            for _ in range(args.boot)
        ]
        or [np.full_like(point, np.nan)]
    )
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)

    print(f"{len(rows)} verdicts, {len(task_names)} tasks")
    print(f"{'system':24} {'Align ELO':>18} {'Design ELO':>18} {'ELO':>18}")
    for k in np.argsort(-point[2]):
        cells = " ".join(f"{point[c, k]:6.0f} [{lo[c, k]:5.0f},{hi[c, k]:5.0f}]" for c in range(3))
        print(f"{systems[k]:24} {cells}")
    if args.out:
        names = ("align_elo", "design_elo", "elo")
        args.out.write_text(
            json.dumps(
                [
                    {"system": systems[k]}
                    | {
                        f"{name}{suffix}": float(v[c, k])
                        for c, name in enumerate(names)
                        for suffix, v in (("", point), ("_lo", lo), ("_hi", hi))
                    }
                    for k in np.argsort(-point[2])
                ],
                indent=1,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
