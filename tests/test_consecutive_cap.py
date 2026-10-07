"""Optional same-family run cap: exhaustive oracle, chains and 422/UNSCHEDULABLE.

Every brute-force case uses at most seven jobs and enumerates all
permutations directly from the problem definition:

* every ordinary precedence edge is respected,
* every immediate pair is literally adjacent,
* no maximal run of equal families exceeds the cap,
* minimum changeovers, ties broken by the UTF-8 byte lexicographic order.

The solver keeps the chain contraction: an over-limit violation may live
inside one chain itself, or only appear once two chains are concatenated, so
the oracle checks the *delivered* linear order rather than any "solve then
repair" artefact.
"""

from __future__ import annotations

import itertools
import random
import time

import pytest

from app.scheduler import Job, solve


# --------------------------------------------------------------------------
# Brute-force oracle
# --------------------------------------------------------------------------


def brute_force(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
    cap: int | None,
) -> dict:
    fam = {j.id: j.family for j in jobs}
    ids = [j.id for j in jobs]
    before_of: dict[str, set[str]] = {jid: set() for jid in ids}
    for u, v in edges:
        before_of[v].add(u)

    best: tuple | None = None
    best_order: list[str] | None = None
    for perm in itertools.permutations(ids):
        position = {jid: i for i, jid in enumerate(perm)}
        placed: set[str] = set()
        ok = True
        for jid in perm:
            if not before_of[jid] <= placed:
                ok = False
                break
            placed.add(jid)
        if not ok:
            continue
        for u, v in immediate:
            if position[v] != position[u] + 1:
                ok = False
                break
        if not ok:
            continue
        if cap is not None:
            run = 1
            for i in range(1, len(perm)):
                if fam[perm[i]] == fam[perm[i - 1]]:
                    run += 1
                else:
                    run = 1
                if run > cap:
                    ok = False
                    break
        if not ok:
            continue
        cost = sum(
            1 for i in range(1, len(perm)) if fam[perm[i]] != fam[perm[i - 1]]
        )
        key = (cost, tuple(x.encode("utf-8") for x in perm))
        if best is None or key < best:
            best = key
            best_order = list(perm)

    if best_order is None:
        return {"feasible": False}
    positions = [
        i + 1
        for i in range(1, len(best_order))
        if fam[best_order[i]] != fam[best_order[i - 1]]
    ]
    return {
        "feasible": True,
        "order": best_order,
        "count": best[0],
        "positions": positions,
    }


def expected_runs(order: list[str], fam: dict[str, str]) -> list[dict]:
    runs: list[dict] = []
    start = 0
    cur = fam[order[0]]
    for i in range(1, len(order) + 1):
        if i == len(order) or fam[order[i]] != cur:
            runs.append(
                {
                    "family": cur,
                    "start": start + 1,
                    "end": i,
                    "length": i - start,
                    "jobs": order[start:i],
                }
            )
            if i < len(order):
                start = i
                cur = fam[order[i]]
    return runs


def check_against_oracle(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
    cap: int,
) -> dict:
    result = solve(jobs, edges, immediate, cap)
    oracle = brute_force(jobs, edges, immediate, cap)
    assert oracle["feasible"], "test expects a feasible instance"
    assert result["status"] == "OK"
    assert result["order"] == oracle["order"]
    assert result["changeover_count"] == oracle["count"]
    assert result["changeover_positions"] == oracle["positions"]
    assert len(result["changeovers"]) == oracle["count"]
    assert result["max_consecutive_same_family"] == cap

    fam = {j.id: j.family for j in jobs}
    runs = result["family_runs"]
    # The segments really are the maximal equal-family segments of the order.
    assert runs == expected_runs(result["order"], fam)
    # Segments partition the order and stay within the enforced cap.
    assert [jid for r in runs for jid in r["jobs"]] == result["order"]
    assert all(r["length"] <= cap for r in runs)
    # Every reported segment is genuinely maximal: neighbouring segments
    # differ in family, and positions are 1-based and contiguous.
    for k, r in enumerate(runs):
        assert r["end"] - r["start"] + 1 == r["length"]
        assert r["jobs"] == result["order"][r["start"] - 1 : r["end"]]
        if k:
            assert runs[k - 1]["family"] != r["family"]
            assert runs[k - 1]["end"] + 1 == r["start"]
    # Immediate pairs stay literally adjacent.
    position = {jid: i for i, jid in enumerate(result["order"])}
    for u, v in immediate:
        assert position[v] == position[u] + 1
    return result


def assert_unschedulable(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
    cap: int,
) -> None:
    assert not brute_force(jobs, edges, immediate, cap)["feasible"]
    assert solve(jobs, edges, immediate, cap) == {"status": "UNSCHEDULABLE"}


# --------------------------------------------------------------------------
# Fixed cases
# --------------------------------------------------------------------------


def test_cross_chain_concatenation_exceeds_cap() -> None:
    # Two immediate chains of family X, length 2 each; one Y singleton.  With
    # cap 3 the chains may not be placed back to back (that would make a run
    # of four X jobs), so the optimum separates them with the Y block:
    # two changeovers.  The unconstrained optimum would glue them (one
    # changeover) -- the cap adjudication must happen inside the DP.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
        Job(id="d", family="X"),
        Job(id="e", family="X"),
    ]
    immediate = [("a", "b"), ("d", "e")]
    result = check_against_oracle(jobs, [], immediate, 3)
    assert result["order"] == ["a", "b", "c", "d", "e"]
    assert result["changeover_count"] == 2
    assert [r["length"] for r in result["family_runs"]] == [2, 1, 2]


def test_cross_chain_concatenation_allowed_when_cap_large() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
        Job(id="d", family="X"),
        Job(id="e", family="X"),
    ]
    immediate = [("a", "b"), ("d", "e")]
    result = check_against_oracle(jobs, [], immediate, 4)
    # Both X chains concatenate into one run of four; Y goes last.
    assert result["order"] == ["a", "b", "d", "e", "c"]
    assert result["changeover_count"] == 1
    assert [(r["family"], r["length"]) for r in result["family_runs"]] == [
        ("X", 4),
        ("Y", 1),
    ]


def test_chain_internal_run_exceeds_cap() -> None:
    # The fixed chain (a,b,c) is all X; cap 2 is violated inside the chain
    # itself, independent of where the chain is placed.  Nothing can split
    # it, so the instance is unschedulable even with a Y job available.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    assert_unschedulable(jobs, [], [("a", "b"), ("b", "c")], 2)


def test_mixed_chain_internal_run_exceeds_cap() -> None:
    # Chain a(X)->b(X)->c(Y)->d(Y)->e(Y) contains a run of three Y jobs.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
        Job(id="d", family="Y"),
        Job(id="e", family="Y"),
        Job(id="f", family="X"),
    ]
    immediate = [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]
    assert_unschedulable(jobs, [], immediate, 2)
    # Cap 3 admits the chain itself; the free X singleton f joins the head
    # run only if that still respects the cap (2+1 = 3).
    result = check_against_oracle(jobs, [], immediate, 3)
    assert result["order"][:5] == ["f", "a", "b", "c", "d"] or result[
        "order"
    ][:5] == ["a", "b", "c", "d", "e"]
    assert max(r["length"] for r in result["family_runs"]) == 3


def test_cap_one_forces_alternation() -> None:
    # Two X, one Y, no other constraints: no two equal families may touch,
    # so the unique shape is X Y X and every adjacency is a changeover.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
    ]
    result = check_against_oracle(jobs, [], [], 1)
    assert result["order"] == ["a", "c", "b"]
    assert result["changeover_count"] == 2
    assert result["changeover_positions"] == [2, 3]
    assert all(r["length"] == 1 for r in result["family_runs"])


def test_cap_one_unschedulable_when_family_dominates() -> None:
    # Three X, one Y: even alternating, some X must touch another X.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    assert_unschedulable(jobs, [], [], 1)


def test_precedence_edge_forces_over_limit_run() -> None:
    # The only two jobs are both X and the edge forces a -> b: with cap 1
    # every adjacency would have to change family, which is impossible here.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
    ]
    edges = [("a", "b")]
    assert_unschedulable(jobs, edges, [], 1)


def test_precedence_edge_conflict_with_cap() -> None:
    # Edges force a,b,c,d into one fixed chain and every job is X, so the
    # whole order is an unavoidable run of four.  The ordinary graph is a
    # DAG and there are no immediate pairs; the cap alone makes the complete
    # instance unschedulable.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="X"),
    ]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    assert_unschedulable(jobs, edges, [], 2)


def test_cap_with_inter_chain_precedence() -> None:
    # Chains (a,c)=X,X and (b,d)=Y,Y; edge c -> b orders chain one first.
    # cap 2 admits both chains; cap 1 is impossible (each chain itself has
    # equal neighbours that can never be split).
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    immediate = [("a", "c"), ("b", "d")]
    result = check_against_oracle(jobs, [("c", "b")], immediate, 2)
    assert result["order"] == ["a", "c", "b", "d"]
    assert result["changeover_count"] == 1
    assert_unschedulable(jobs, [("c", "b")], immediate, 1)


def test_cycle_semantics_unchanged_with_cap() -> None:
    # A precedence cycle still answers CYCLE (with its evidence) even when
    # the cap field is present and the same graph would also strain the cap.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
    ]
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    result = solve(jobs, edges, None, 1)
    assert result["status"] == "CYCLE"
    assert result["cycle"] == ["a", "b", "c"]
    assert set(result) == {"status", "cycle"}


def test_immediate_only_conflict_still_unschedulable_with_cap() -> None:
    # Pre-existing immediate contradictions keep their verdict; the cap
    # never turns them into a partial schedule.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Z"),
    ]
    assert solve(jobs, [], [("a", "a")], 2) == {"status": "UNSCHEDULABLE"}
    assert solve(
        jobs, [("a", "b"), ("b", "c")], [("a", "c")], 2
    ) == {"status": "UNSCHEDULABLE"}


def test_omitting_cap_leaves_payload_untouched() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    edges = [("a", "b")]
    without = solve(jobs, edges)
    assert set(without) == {
        "status",
        "order",
        "changeover_count",
        "changeover_positions",
        "changeovers",
    }
    assert solve(jobs, edges, None, None) == without
    # Enabling the cap adds exactly the two review fields.
    with_cap = solve(jobs, edges, None, 2)
    extra = set(with_cap) - set(without)
    assert extra == {"max_consecutive_same_family", "family_runs"}
    for key in without:
        assert with_cap[key] == without[key]


# --------------------------------------------------------------------------
# Random exhaustive (n <= 7) cross-checks
# --------------------------------------------------------------------------


def random_instance(
    rng: random.Random, n: int, families: list[str]
) -> tuple[list[Job], list[tuple[str, str]], list[tuple[str, str]]]:
    ids = [f"w{i:02d}" for i in range(n)]
    fams = [rng.choice(families) for _ in range(n)]
    jobs = [Job(id=ids[i], family=fams[i]) for i in range(n)]
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.32:
                edges.add((ids[i], ids[j]))
    perm = ids[:]
    rng.shuffle(perm)
    immediate: list[tuple[str, str]] = []
    i = 0
    while i < n - 1:
        if rng.random() < 0.5:
            run = rng.randint(2, min(4, n - i))
            for k in range(run - 1):
                immediate.append((perm[i + k], perm[i + k + 1]))
            i += run
        else:
            i += 1
    return jobs, sorted(edges), immediate


@pytest.mark.parametrize(
    "seed,families",
    [(20261007, ["A", "B", "C"]), (20261008, ["A", "B"])],
)
def test_random_small_graphs_against_oracle(
    seed: int, families: list[str]
) -> None:
    rng = random.Random(seed)
    feasible = infeasible = 0
    for _ in range(900):
        n = rng.randint(2, 7)
        jobs, edges, immediate = random_instance(rng, n, families)
        cap = rng.choice([1, 1, 2, 2, 3, n])
        oracle = brute_force(jobs, edges, immediate, cap)
        result = solve(jobs, edges, immediate, cap)
        if oracle["feasible"]:
            assert result["status"] == "OK", (n, edges, immediate, cap, result)
            assert result["order"] == oracle["order"], (
                n,
                edges,
                immediate,
                cap,
            )
            assert result["changeover_count"] == oracle["count"]
            assert result["changeover_positions"] == oracle["positions"]
            fam = {j.id: j.family for j in jobs}
            assert result["family_runs"] == expected_runs(
                result["order"], fam
            )
            feasible += 1
        else:
            assert result == {"status": "UNSCHEDULABLE"}, (
                n,
                edges,
                immediate,
                cap,
                result,
            )
            infeasible += 1
    assert feasible > 0 and infeasible > 0


def test_eighteen_jobs_with_cap_runs_fast() -> None:
    jobs = [
        Job(id=f"J{i:02d}", family="A" if i % 2 == 0 else "B")
        for i in range(18)
    ]
    start = time.perf_counter()
    result = solve(jobs, [], None, 9)
    elapsed = time.perf_counter() - start
    assert result["status"] == "OK"
    assert elapsed < 15.0
    assert all(r["length"] <= 9 for r in result["family_runs"])
