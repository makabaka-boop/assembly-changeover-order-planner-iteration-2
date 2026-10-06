"""Same-family run cap: exhaustive oracle, chain boundary cases, ties.

Every cross-check uses at most seven jobs and the brute-force oracle
enumerates all permutations directly from the problem definition:

* every ordinary precedence edge is respected,
* every immediate pair is literally adjacent,
* no maximal run of equal-family consecutive jobs exceeds the cap,
* minimum changeovers, ties broken by the UTF-8 byte lexicographic order.

The tricky structural cases (which a "solve the old optimum, then repair"
approach would miss):

* a chain already violates the cap internally -> always UNSCHEDULABLE;
* neither chain overflows, but stitching two same-family chains does ->
  UNSCHEDULABLE or a forced extra changeover depending on the jobs available;
* ordinary precedence edges can themselves force the overflow.
"""

from __future__ import annotations

import itertools
import random
import time

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
        placed: set[str] = set()
        ok = True
        for jid in perm:
            if not before_of[jid] <= placed:
                ok = False
                break
            placed.add(jid)
        if not ok:
            continue
        position = {jid: i for i, jid in enumerate(perm)}
        for u, v in immediate:
            if position[v] != position[u] + 1:
                ok = False
                break
        if not ok:
            continue
        if cap is not None:
            run = 0
            for i, jid in enumerate(perm):
                if i == 0 or fam[jid] != fam[perm[i - 1]]:
                    run = 1
                else:
                    run += 1
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


def check_against_oracle(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
    cap: int,
) -> dict:
    result = solve(jobs, edges, immediate, max_same_family_run=cap)
    oracle = brute_force(jobs, edges, immediate, cap)
    assert oracle["feasible"], "test expected a feasible instance"
    assert result["status"] == "OK", result
    assert result["order"] == oracle["order"]
    assert result["changeover_count"] == oracle["count"]
    assert result["changeover_positions"] == oracle["positions"]
    assert len(result["changeovers"]) == oracle["count"]

    # All reported runs partition the order and obey the cap.
    runs = result["family_runs"]
    assert [jid for seg in runs for jid in seg["jobs"]] == result["order"]
    fams = {j.id: j.family for j in jobs}
    for i, seg in enumerate(runs):
        assert seg["length"] == len(seg["jobs"])
        assert seg["length"] <= cap
        assert seg["start"] >= 1
        assert seg["end"] == seg["start"] + seg["length"] - 1
        for jid in seg["jobs"]:
            assert fams[jid] == seg["family"]
        if i > 0:
            # Adjacent segments really are different families.
            assert runs[i - 1]["family"] != seg["family"]
    # Run boundaries coincide with changeover positions.
    boundaries = [seg["start"] for seg in runs[1:]]
    assert boundaries == result["changeover_positions"]

    # Every immediate pair stays literally adjacent.
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
    result = solve(jobs, edges, immediate, max_same_family_run=cap)
    assert result == {"status": "UNSCHEDULABLE"}


# --------------------------------------------------------------------------
# Fixed structural cases
# --------------------------------------------------------------------------


def test_chain_internal_overflow_is_unschedulable() -> None:
    # The fixed chain a -> b -> c is three X jobs; cap 2 is violated inside
    # the chain regardless of where the block is placed.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    assert_unschedulable(jobs, [], [("a", "b"), ("b", "c")], 2)


def test_cross_chain_overflow_without_separator_is_unschedulable() -> None:
    # Two X chains (a,c) and (b,d): each is length 2 (legal alone), but any
    # stitching that keeps them adjacent makes a run of 4, and there is no
    # non-X job in the instance to put between them.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="X"),
    ]
    assert_unschedulable(jobs, [], [("a", "c"), ("b", "d")], 2)


def test_cross_chain_overflow_forces_extra_changeover() -> None:
    # Chains (a,b) is two X jobs; c is X, d is Y.  Without the cap the free
    # optimum groups all X (one changeover), which puts the run at 3; with
    # cap 2 the Y job must split the run, costing one extra changeover.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    free = solve(jobs, [], [("a", "b")])
    assert free["changeover_count"] == 1
    result = check_against_oracle(jobs, [], [("a", "b")], 2)
    assert result["order"] == ["a", "b", "d", "c"]
    assert result["changeover_count"] == 2
    assert [seg["length"] for seg in result["family_runs"]] == [2, 1, 1]


def test_ordinary_precedence_edges_force_overflow() -> None:
    # No immediate pairs at all: the ordinary edges a -> b -> c force a run of
    # three X jobs, and with no other family in the instance nothing can be
    # inserted between them -> cap 2 impossible.  (With a free Y job the same
    # edges would be feasible by inserting it inside the chain, so it is
    # omitted here on purpose.)
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
    ]
    assert_unschedulable(jobs, [("a", "b"), ("b", "c")], [], 2)


def test_precedence_edge_between_chains_keeps_overflow() -> None:
    # Chains (a,c) and (b,d), all X, plus edge c -> b forcing the two chains
    # to concatenate: run length 4, cap 2 impossible.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="X"),
    ]
    assert_unschedulable(jobs, [("c", "b")], [("a", "c"), ("b", "d")], 2)


def test_cap_one_forces_alternation() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    result = check_against_oracle(jobs, [], [], 1)
    assert result["order"] == ["a", "b", "c"]
    assert result["changeover_count"] == 2
    assert [seg["length"] for seg in result["family_runs"]] == [1, 1, 1]


def test_cap_one_all_same_family_is_unschedulable() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
    ]
    assert_unschedulable(jobs, [], [], 1)


def test_multi_run_chain_merges_at_boundary() -> None:
    # Chain (a,b) has families X,Y; singleton c is X.  Cap 1 forbids merging
    # c with the chain's X end directly, so the oracle-pinned order keeps a
    # changeover at the boundary instead of extending the X run.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="X"),
    ]
    # cap 1: the chain itself is fine (X then Y), but c,d are two free X
    # singletons and only one Y exists -- the X jobs can never be separated
    # pairwise, so check the feasible variant with cap 2.
    result = check_against_oracle(jobs, [], [("a", "b")], 2)
    assert result["status"] == "OK"
    for seg in result["family_runs"]:
        assert seg["length"] <= 2


def test_tie_break_keeps_byte_order_among_optima() -> None:
    # Three X singletons and one Y job, cap 2: exactly one extra changeover is
    # needed and the lex-smallest optimum puts the separator after the first
    # two X ids.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    result = check_against_oracle(jobs, [], [], 2)
    assert result["order"] == ["a", "b", "d", "c"]
    assert result["changeover_count"] == 2


def test_cycle_takes_precedence_over_cap() -> None:
    # An ordinary precedence cycle keeps the original CYCLE semantics even
    # when the cap would also be violated.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
    ]
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    result = solve(jobs, edges, [], max_same_family_run=1)
    assert result["status"] == "CYCLE"
    assert result["cycle"] == ["a", "b", "c"]
    assert "order" not in result


def test_unschedulable_response_has_no_partial_schedule() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
    ]
    result = solve(jobs, [], [("a", "b"), ("b", "c")], max_same_family_run=2)
    assert result == {"status": "UNSCHEDULABLE"}


def test_cap_above_run_lengths_does_not_change_order() -> None:
    # A generous cap changes feasibility but never the optimum: the delivered
    # order, changeover count and positions equal the uncapped solution.
    rng = random.Random(20261006)
    ids = [f"w{i:02d}" for i in range(6)]
    families = [rng.choice(["A", "B", "C"]) for _ in ids]
    jobs = [Job(id=ids[i], family=families[i]) for i in range(6)]
    edges = {(ids[i], ids[j])
             for i in range(6) for j in range(i + 1, 6)
             if rng.random() < 0.3}
    uncapped = solve(jobs, sorted(edges))
    capped = solve(jobs, sorted(edges), [], max_same_family_run=6)
    assert capped["order"] == uncapped["order"]
    assert capped["changeover_count"] == uncapped["changeover_count"]
    assert capped["changeover_positions"] == uncapped["changeover_positions"]
    assert capped["changeovers"] == uncapped["changeovers"]


def test_disabled_cap_payload_is_unchanged_field_by_field() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
    ]
    omitted = solve(jobs, [("a", "b")])
    none_explicit = solve(jobs, [("a", "b")], None, None)
    assert none_explicit == omitted
    assert "family_runs" not in omitted


# --------------------------------------------------------------------------
# Random exhaustive (n <= 7) cross-checks
# --------------------------------------------------------------------------


def random_instance(
    rng: random.Random, n: int, num_families: int
) -> tuple[list[Job], list[tuple[str, str]], list[tuple[str, str]]]:
    ids = [f"w{i:02d}" for i in range(n)]
    families = [
        rng.choice([chr(ord("A") + k) for k in range(num_families)])
        for _ in range(n)
    ]
    jobs = [Job(id=ids[i], family=families[i]) for i in range(n)]
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.3:
                edges.add((ids[i], ids[j]))
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
    rng = random.Random(20261006)
    feasible = infeasible = 0
    for n in range(2, 8):
        for _ in range(60):
            num_families = rng.choice([1, 2, 2, 3, 4])
            jobs, edges, immediate = random_instance(rng, n, num_families)
            cap = rng.choice([1, 1, 2, 2, 3, n])
            oracle = brute_force(jobs, edges, immediate, cap)
            result = solve(jobs, edges, immediate, max_same_family_run=cap)
            if oracle["feasible"]:
                assert result["status"] == "OK", (n, edges, immediate, cap, result)
                assert result["order"] == oracle["order"]
                assert result["changeover_count"] == oracle["count"]
                assert result["changeover_positions"] == oracle["positions"]
                for seg in result["family_runs"]:
                    assert seg["length"] <= cap
                feasible += 1
            else:
                assert result == {"status": "UNSCHEDULABLE"}, result
                infeasible += 1
    assert feasible > 0 and infeasible > 0


def test_eighteen_jobs_capped_run_fast() -> None:
    jobs = [
        Job(id=f"J{i:02d}", family="A" if i % 2 == 0 else "B")
        for i in range(18)
    ]
    start = time.perf_counter()
    result = solve(jobs, [], [], max_same_family_run=2)
    elapsed = time.perf_counter() - start
    assert result["status"] == "OK"
    assert elapsed < 15.0
    for seg in result["family_runs"]:
        assert seg["length"] <= 2
