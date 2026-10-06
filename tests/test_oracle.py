"""Exhaustive cross-checks against a brute-force oracle on small graphs."""

from __future__ import annotations

import itertools
import random

from app.scheduler import Job, solve


def brute_force(jobs: list[Job], edges: list[tuple[str, str]]) -> dict:
    """Enumerate every permutation; return the optimum defined by the spec."""
    fam = {j.id: j.family for j in jobs}
    ids = [j.id for j in jobs]
    before_of: dict[str, set[str]] = {jid: set() for jid in ids}
    for u, v in edges:
        before_of[v].add(u)

    best: tuple | None = None
    best_order: list[str] | None = None
    for perm in itertools.permutations(ids):
        placed: set[str] = set()
        ok = True
        for jid in perm:
            if not before_of[jid] <= placed:
                ok = False
                break
            placed.add(jid)
        if not ok:
            continue
        cost = sum(
            1 for i in range(1, len(perm)) if fam[perm[i]] != fam[perm[i - 1]]
        )
        key = (cost, tuple(x.encode("utf-8") for x in perm))
        if best is None or key < best:
            best = key
            best_order = list(perm)
    assert best_order is not None
    positions = [
        i + 1
        for i in range(1, len(best_order))
        if fam[best_order[i]] != fam[best_order[i - 1]]
    ]
    return {"order": best_order, "count": best[0], "positions": positions}


def random_dag(rng: random.Random, n: int) -> tuple[list[Job], list[tuple[str, str]]]:
    ids = [f"w{i:02d}" for i in range(n)]
    families = [rng.choice(["A", "B", "C"]) for _ in range(n)]
    jobs = [Job(id=ids[i], family=families[i]) for i in range(n)]
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.35:
                edges.add((ids[i], ids[j]))
    return jobs, sorted(edges)


def check_against_oracle(jobs: list[Job], edges: list[tuple[str, str]]) -> None:
    result = solve(jobs, edges)
    assert result["status"] == "OK"
    oracle = brute_force(jobs, edges)
    assert result["order"] == oracle["order"]
    assert result["changeover_count"] == oracle["count"]
    assert result["changeover_positions"] == oracle["positions"]
    assert len(result["changeovers"]) == oracle["count"]
    for detail in result["changeovers"]:
        pos = detail["position"]
        assert pos in result["changeover_positions"]
        assert detail["from"]["id"] == result["order"][pos - 2]
        assert detail["to"]["id"] == result["order"][pos - 1]
        assert detail["from"]["family"] != detail["to"]["family"]


def test_fixed_cases() -> None:
    # Chain forces one order; identical family -> no changeovers.
    check_against_oracle(
        [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="c", family="X")],
        [("a", "b"), ("b", "c")],
    )
    # Two independent families: lex-smallest groups them into one changeover.
    check_against_oracle(
        [Job(id="1", family="X"), Job(id="2", family="Y"), Job(id="3", family="X")],
        [],
    )
    # Constraint that fights naive family grouping.
    check_against_oracle(
        [
            Job(id="a", family="X"),
            Job(id="b", family="Y"),
            Job(id="c", family="X"),
            Job(id="d", family="Y"),
        ],
        [("b", "a"), ("a", "d")],
    )


def test_exhaustive_random_small_graphs() -> None:
    rng = random.Random(20260924)
    for n in range(2, 8):
        for _ in range(40):
            jobs, edges = random_dag(rng, n)
            check_against_oracle(jobs, edges)


def test_distinct_families_chain_break() -> None:
    # Every family unique -> every adjacency is a changeover, order is forced.
    jobs = [Job(id=str(i), family=f"f{i}") for i in range(5)]
    edges = [(str(i), str(i + 1)) for i in range(4)]
    result = solve(jobs, edges)
    assert result["order"] == ["0", "1", "2", "3", "4"]
    assert result["changeover_count"] == 4
    assert result["changeover_positions"] == [2, 3, 4, 5]


def test_eighteen_jobs_runs_fast() -> None:
    import time

    jobs = [Job(id=f"J{i:02d}", family="A" if i % 2 == 0 else "B") for i in range(18)]
    start = time.perf_counter()
    result = solve(jobs, [])
    elapsed = time.perf_counter() - start
    assert result["status"] == "OK"
    assert result["changeover_count"] == 1
    # All even ids precede all odd ids (each block internally byte-sorted).
    evens = [j.id for j in jobs if j.family == "A"]
    odds = [j.id for j in jobs if j.family == "B"]
    assert result["order"] == sorted(evens) + sorted(odds)
    assert elapsed < 15.0
