"""Cycle evidence tests: real edges, rotation starts at the smallest id."""

from __future__ import annotations

from app.scheduler import Job, solve


def assert_valid_cycle(
    jobs: list[Job], edges: list[tuple[str, str]], expected: list[str]
) -> None:
    edge_set = set(edges)
    result = solve(jobs, edges)
    assert result["status"] == "CYCLE"
    cycle = result["cycle"]
    assert cycle == expected
    # Evidence must start at its own minimum id (UTF-8 byte order).
    assert cycle[0] == min(cycle, key=lambda s: s.encode("utf-8"))
    # Every listed edge must exist in the input.
    for i in range(len(cycle)):
        u = cycle[i]
        v = cycle[(i + 1) % len(cycle)]
        assert (u, v) in edge_set, f"cycle edge {u}->{v} not in input"
    # CYCLE responses must never contain a partial schedule.
    assert "order" not in result
    assert "changeover_count" not in result


def test_self_loop() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y")]
    assert_valid_cycle(jobs, [("a", "b"), ("b", "b")], ["b"])


def test_simple_three_node_cycle() -> None:
    jobs = [Job(id="c", family="X"), Job(id="a", family="Y"), Job(id="b", family="Z")]
    assert_valid_cycle(jobs, [("a", "b"), ("b", "c"), ("c", "a")], ["a", "b", "c"])


def test_cycle_start_not_dfs_root() -> None:
    # Tail z -> x enters a cycle y <-> z; evidence must rotate to y.
    jobs = [Job(id="x", family="X"), Job(id="y", family="Y"), Job(id="z", family="Z")]
    assert_valid_cycle(
        jobs,
        [("x", "y"), ("y", "z"), ("z", "y")],
        ["y", "z"],
    )


def test_smallest_of_several_cycles_chosen() -> None:
    # Edges contain cycle d->e->d and a->b->c->a; smaller id-start wins.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
        Job(id="e", family="Y"),
    ]
    assert_valid_cycle(
        jobs,
        [("a", "b"), ("b", "c"), ("c", "a"), ("d", "e"), ("e", "d")],
        ["a", "b", "c"],
    )


def test_byte_order_rotation() -> None:
    # ids "10" < "9" in UTF-8 byte order.
    jobs = [Job(id="10", family="X"), Job(id="9", family="Y")]
    assert_valid_cycle(jobs, [("9", "10"), ("10", "9")], ["10", "9"])
