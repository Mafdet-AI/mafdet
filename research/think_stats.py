"""Statistics for thinking ahead's accuracy runs (`mafdet/proxy/bench.py --json-out` files).

Small error rates, few samples per problem, several problems: a run's "8/8" says little (2026-11-14: a
variant went 24/24, then 9/15 on the tip problem when repeated). So, per configuration and per problem:

  * Wilson 95% intervals (not the normal approximation: Bowyer, Aitchison, Ivanova 2025, arXiv:2503.01747);
  * P(A better than B): Beta(1+right, 1+wrong) posteriors, Monte Carlo;
  * Cochran-Mantel-Haenszel across problems (stratified 2x2 tables, 1 df, continuity-corrected);
  * Wald's SPRT per problem, error p0 = 2% against p1 = 20% (alpha 0.05, beta 0.2): two early errors reject,
    a long clean run accepts;
  * AUROC of a per-turn score (the verifier's P(yes)) for "the answer is right", Mann-Whitney.

Usage:
    python research/think_stats.py A.json B.json ...        # each file is one configuration (ON rows)
    python research/think_stats.py A.json --side off        # the OFF rows of a file
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

Z = 1.959963984540054


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / den
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def p_better(ka: int, na: int, kb: int, nb: int, draws: int = 200_000, seed: int = 0) -> float:
    """P(accuracy of A > accuracy of B) under Beta(1+k, 1+n-k) posteriors."""
    rng = np.random.default_rng(seed)
    a = rng.beta(1 + ka, 1 + na - ka, draws)
    b = rng.beta(1 + kb, 1 + nb - kb, draws)
    return float((a > b).mean())


def cmh(tables: list[tuple[int, int, int, int]]) -> tuple[float, float, float]:
    """Cochran-Mantel-Haenszel over per-problem tables (a=A wrong, b=A right, c=B wrong, d=B right):
    (odds ratio of A's error against B's, chi-square, p-value)."""
    num = den = s = v = 0.0
    for a, b, c, d in tables:
        n = a + b + c + d
        if n < 2:
            continue
        num += a * d / n
        den += b * c / n
        s += a - (a + b) * (a + c) / n
        v += (a + b) * (c + d) * (a + c) * (b + d) / (n * n * (n - 1))
    orr = num / den if den else float("inf") if num else float("nan")
    chi = (abs(s) - 0.5) ** 2 / v if v else 0.0
    return orr, chi, math.erfc(math.sqrt(chi / 2))      # the chi-square tail with 1 degree of freedom


def sprt(outcomes: list[bool], p0: float = 0.02, p1: float = 0.20, alpha: float = 0.05,
         beta: float = 0.2) -> str:
    """Wald's SPRT on a sequence of right (True) / wrong (False) outcomes for "error rate is p0" (fine)
    against "error rate is p1" (not fine)."""
    up, low = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    s = 0.0
    for i, ok in enumerate(outcomes, 1):
        s += math.log((1 - p1) / (1 - p0)) if ok else math.log(p1 / p0)
        if s >= up:
            return f"REJECT after {i}"
        if s <= low:
            return f"ACCEPT after {i}"
    return f"undecided ({len(outcomes)} runs, LLR {s:+.2f})"


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    # Mann-Whitney: the share of (right, wrong) pairs where the right one scores higher, ties counted half
    wins = sum((x > y) + 0.5 * (x == y) for x in pos for y in neg)
    return wins / (len(pos) * len(neg))


def load(path: str, side: str) -> list[dict]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]["typed"]
    return [r for r in rows if r.get(f"{side}_right") is not None]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--side", default="on", choices=["on", "off"])
    a = ap.parse_args()
    configs = {Path(f).stem: load(f, a.side) for f in a.files}
    for name, rows in configs.items():
        k, n = sum(r[f"{a.side}_right"] for r in rows), len(rows)
        lo, hi = wilson(k, n)
        waits = [r.get(f"{a.side}_answer_s") for r in rows if r.get(f"{a.side}_answer_s")]
        extra = ""
        if rows and "branches_agreed" in rows[0] or any("branches_agreed" in r for r in rows):
            agreed = sum(1 for r in rows if r.get("branches_agreed"))
            fb = sum(1 for r in rows if r.get("fallback"))
            extra += f"  agreed {agreed}/{n}, fallback {fb}"
        p = [r.get("verify_p_yes") for r in rows]
        if any(x is not None for x in p):
            pairs = [(x, bool(r[f"{a.side}_right"])) for x, r in zip(p, rows) if x is not None]
            au = auroc([x for x, _ in pairs], [y for _, y in pairs])
            extra += f"  verifier AUROC {au if au is None else round(au, 3)} (n {len(pairs)})"
        print(f"{name:24s} right {k}/{n} = {k / max(n, 1):.3f}  Wilson [{lo:.3f}, {hi:.3f}]"
              f"  wait median {np.median(waits) if waits else float('nan'):.2f} s{extra}")
        per = defaultdict(list)
        for r in rows:
            per[r["problem"]].append(bool(r[f"{a.side}_right"]))
        for prob, outs in sorted(per.items()):
            lo, hi = wilson(sum(outs), len(outs))
            print(f"    problem {prob:2d}: {sum(outs)}/{len(outs)}  [{lo:.2f}, {hi:.2f}]  SPRT {sprt(outs)}")
    names = list(configs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, B = configs[names[i]], configs[names[j]]
            ka, na = sum(r[f"{a.side}_right"] for r in A), len(A)
            kb, nb = sum(r[f"{a.side}_right"] for r in B), len(B)
            pa, pb = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
            for r in A:
                pa[r["problem"]][0 if r[f"{a.side}_right"] else 1] += 1
            for r in B:
                pb[r["problem"]][0 if r[f"{a.side}_right"] else 1] += 1
            tables = [(pa[p][1], pa[p][0], pb[p][1], pb[p][0]) for p in set(pa) & set(pb)]
            orr, chi, pv = cmh(tables)
            print(f"{names[i]} vs {names[j]}: P({names[i]} better) = {p_better(ka, na, kb, nb):.3f};"
                  f"  CMH odds ratio of error {orr:.2f}, p = {pv:.3f} ({len(tables)} problems)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
