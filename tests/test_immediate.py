"""Immediate-adjacency feature: exhaustive oracle, chains and UNSCHEDULABLE.

Every case uses at most seven jobs, and the brute-force oracle enumerates
all permutations directly from the problem definition:

* every ordinary precedence edge is respected,
* every immediate pair is literally adjacent,
* minimum changeovers, ties broken by the UTF-8 byte lexicographic order.
"""

from __future__ import annotations

import itertools
import random

import pytest

from app.scheduler import Job, solve


def brute_force(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
) -> dict:
    """Enumerate every permutation; return the optimum or an infeasibility."""
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


def check_against_oracle(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
) -> dict:
    result = solve(jobs, edges, immediate)
    oracle = brute_force(jobs, edges, immediate)
    assert oracle["feasible"]
    assert result["status"] == "OK"
    assert result["order"] == oracle["order"]
    assert result["changeover_count"] == oracle["count"]
    assert result["changeover_positions"] == oracle["positions"]
    assert len(result["changeovers"]) == oracle["count"]
    # All immediate pairs must be literally adjacent in the delivered order.
    position = {jid: i for i, jid in enumerate(result["order"])}
    for u, v in immediate:
        assert position[v] == position[u] + 1
    for detail in result["changeovers"]:
        pos = detail["position"]
        assert pos in result["changeover_positions"]
        assert detail["from"]["id"] == result["order"][pos - 2]
        assert detail["to"]["id"] == result["order"][pos - 1]
        assert detail["from"]["family"] != detail["to"]["family"]
    return result


def assert_unschedulable(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
) -> None:
    assert not brute_force(jobs, edges, immediate)["feasible"]
    result = solve(jobs, edges, immediate)
    assert result == {"status": "UNSCHEDULABLE"}


# --------------------------------------------------------------------------
# Fixed cases
# --------------------------------------------------------------------------


def test_single_immediate_pair_no_other_constraints() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    check_against_oracle(jobs, [], [("a", "c")])


def test_chain_three_jobs() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Y"),
    ]
    result = check_against_oracle(jobs, [], [("a", "b"), ("b", "c")])
    assert result["order"] == ["a", "b", "c"]
    assert result["changeover_count"] == 1
    assert result["changeover_positions"] == [2]


def test_inter_chain_dependency() -> None:
    # Two chains (a,c) and (b,d); ordinary edge c -> b forces the first chain
    # before the second, so the family-blocking optimum c..b.. must yield to
    # the joint adjudication.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    result = check_against_oracle(
        jobs, [("c", "b")], [("a", "c"), ("b", "d")]
    )
    assert result["order"] == ["a", "c", "b", "d"]


def test_inter_chain_dependency_reversed_forces_unschedulable() -> None:
    # Two chains (a,c) and (b,d).  Edge d -> a says chain (b,d) precedes
    # chain (a,c), while edge c -> b says the opposite: the block graph has
    # a cycle even though every ordinary edge points between distinct jobs
    # and the original graph is acyclic.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    assert_unschedulable(
        jobs, [("d", "a"), ("c", "b")], [("a", "c"), ("b", "d")]
    )


def test_looks_acyclic_but_not_adjacent() -> None:
    # Ordinary graph a -> b -> c is a DAG, but immediate a -> c demands that
    # nothing sit between a and c; b must, so the instance is unschedulable.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    assert_unschedulable(jobs, [("a", "b"), ("b", "c")], [("a", "c")])


def test_self_immediate_pair_is_unschedulable() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y")]
    assert_unschedulable(jobs, [], [("a", "a")])


def test_immediate_only_cycle() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Z"),
    ]
    assert_unschedulable(jobs, [], [("a", "b"), ("b", "c"), ("c", "a")])


def test_two_node_immediate_cycle() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y")]
    assert_unschedulable(jobs, [], [("a", "b"), ("b", "a")])


def test_block_level_cycle() -> None:
    # Immediate chain a -> b and ordinary edge b -> a: the ordinary graph is
    # acyclic, but the contracted blocks form a self-loop.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Z"),
    ]
    assert_unschedulable(jobs, [("b", "a")], [("a", "b")])


def test_backward_edge_inside_chain() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Z"),
    ]
    assert_unschedulable(jobs, [("c", "a")], [("a", "b"), ("b", "c")])


def test_precedence_cycle_still_reported_as_cycle() -> None:
    # Even with valid immediate pairs, an ordinary precedence cycle keeps
    # its original CYCLE verdict and evidence.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="Z"),
    ]
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    result = solve(jobs, edges, [("a", "b")])
    assert result["status"] == "CYCLE"
    assert result["cycle"] == ["a", "b", "c"]
    assert "order" not in result


def test_joint_adjudication_not_repair() -> None:
    # Families: a=Y, b=c=d=X; ordinary edge a -> c.  Without the immediate
    # pair the free optimum is a,b,c,d (one changeover), which separates d
    # and b -- the immediate demand d -> b is violated there.  No local move
    # of jobs inside a,b,c,d produces a feasible 1-changeover order: the
    # solver must adjudicate chains and inter-chain edges together and find
    # a,c,d,b.
    jobs = [
        Job(id="a", family="Y"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="X"),
    ]
    free = solve(jobs, [("a", "c")], [])
    assert free["order"] == ["a", "b", "c", "d"]
    assert free["changeover_count"] == 1
    result = check_against_oracle(jobs, [("a", "c")], [("d", "b")])
    assert result["order"] == ["a", "c", "d", "b"]
    assert result["changeover_count"] == 1


def test_changeover_tie_broken_by_full_id_sequence() -> None:
    # Chain (a,b) has families X,Y; singletons c=X, d=Y.  The cheapest
    # orders place c in front of the chain to stay in family X.  The oracle
    # pins the exact lexicographic answer.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    result = check_against_oracle(jobs, [], [("a", "b")])
    assert result["order"] == ["c", "a", "b", "d"]
    assert result["changeover_count"] == 1


# --------------------------------------------------------------------------
# Random exhaustive (n <= 7) cross-checks
# --------------------------------------------------------------------------


def random_instance(
    rng: random.Random, n: int
) -> tuple[list[Job], list[tuple[str, str]], list[tuple[str, str]]]:
    ids = [f"w{i:02d}" for i in range(n)]
    families = [rng.choice(["A", "B", "C"]) for _ in range(n)]
    jobs = [Job(id=ids[i], family=families[i]) for i in range(n)]
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.3:
                edges.add((ids[i], ids[j]))
    # Degree-respecting immediate pairs from a shuffled permutation: disjoint
    # chains, possibly leaving singleton jobs.
    perm = ids[:]
    rng.shuffle(perm)
    immediate: list[tuple[str, str]] = []
    i = 0
    while i < n - 1:
        if rng.random() < 0.5:
            immediate.append((perm[i], perm[i + 1]))
            i += 1
        else:
            i += 1
    return jobs, sorted(edges), immediate


def test_random_small_graphs_against_oracle() -> None:
    rng = random.Random(20260926)
    feasible = infeasible = 0
    for n in range(2, 8):
        for _ in range(50):
            jobs, edges, immediate = random_instance(rng, n)
            oracle = brute_force(jobs, edges, immediate)
            result = solve(jobs, edges, immediate)
            if oracle["feasible"]:
                assert result["status"] == "OK", (n, edges, immediate, result)
                assert result["order"] == oracle["order"]
                assert result["changeover_count"] == oracle["count"]
                assert result["changeover_positions"] == oracle["positions"]
                position = {
                    jid: i for i, jid in enumerate(result["order"])
                }
                for u, v in immediate:
                    assert position[v] == position[u] + 1
                feasible += 1
            else:
                assert result == {"status": "UNSCHEDULABLE"}, result
                infeasible += 1
    # Sanity: both kinds of outcomes must actually occur in the sample.
    assert feasible > 0 and infeasible > 0


def test_eighteen_job_chains_run_fast() -> None:
    import time

    jobs = [
        Job(id=f"J{i:02d}", family="A" if i % 2 == 0 else "B")
        for i in range(18)
    ]
    immediate = [("J00", "J02"), ("J02", "J04"), ("J01", "J03")]
    start = time.perf_counter()
    result = solve(jobs, [], immediate)
    elapsed = time.perf_counter() - start
    assert result["status"] == "OK"
    assert elapsed < 15.0
    position = {jid: i for i, jid in enumerate(result["order"])}
    for u, v in immediate:
        assert position[v] == position[u] + 1


@pytest.mark.parametrize("as_empty_list", [False, True])
def test_omitting_immediate_matches_old_behaviour(as_empty_list: bool) -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    edges = [("a", "b")]
    without = solve(jobs, edges)
    with_field = solve(jobs, edges, [] if as_empty_list else None)
    assert with_field == without
