"""HTTP-level tests: 422 validation, CYCLE response and success payload."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body: object) -> object:
    return client.post("/schedule", json=body)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_basic_success() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "Y"},
        ],
        "edges": [{"before": "a", "after": "b"}],
    }
    resp = post(body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["order"] == ["a", "b", "c"]
    assert data["changeover_count"] == 1
    assert data["changeover_positions"] == [3]
    detail = data["changeovers"][0]
    assert detail["position"] == 3
    assert detail["from"] == {"id": "b", "family": "X"}
    assert detail["to"] == {"id": "c", "family": "Y"}


def test_cycle_response_has_no_partial_schedule() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
            {"id": "c", "family": "Z"},
        ],
        "edges": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "c"},
            {"before": "c", "after": "a"},
        ],
    }
    resp = post(body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CYCLE"
    assert data["cycle"] == ["a", "b", "c"]
    assert set(data) == {"status", "cycle"}


def _expect_422(body: object) -> None:
    resp = post(body)
    assert resp.status_code == 422, resp.text


def test_rejects_self_loop() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [{"before": "a", "after": "a"}],
        }
    )


def test_rejects_duplicate_edge() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [
                {"before": "a", "after": "b"},
                {"before": "a", "after": "b"},
            ],
        }
    )


def test_rejects_unknown_reference() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [{"before": "a", "after": "zzz"}],
        }
    )


def test_rejects_extra_fields() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X", "priority": 1},
                {"id": "b", "family": "Y"},
            ],
            "edges": [],
        }
    )
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
            ],
            "edges": [{"before": "a", "after": "b", "weight": 5}],
        }
    )
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
            ],
            "edges": [],
            "mode": "fast",
        }
    )


def test_rejects_bad_job_counts() -> None:
    _expect_422({"jobs": [{"id": "a", "family": "X"}], "edges": []})
    _expect_422(
        {
            "jobs": [{"id": f"j{i}", "family": "F"} for i in range(19)],
            "edges": [],
        }
    )


def test_rejects_duplicate_ids_empty_and_non_ascii() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "a", "family": "Y"}],
            "edges": [],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "", "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "a", "family": ""}, {"id": "b", "family": "Y"}],
            "edges": [],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "aé", "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [],
        }
    )


def test_rejects_missing_and_wrong_types() -> None:
    _expect_422({"edges": []})
    _expect_422({"jobs": [{"id": "a"}, {"id": "b"}], "edges": []})
    _expect_422(
        {
            "jobs": [{"id": 1, "family": "X"}, {"id": "b", "family": "Y"}],
            "edges": [],
        }
    )


def test_rejects_more_than_100_edges() -> None:
    edges = [
        {"before": f"j{i:02d}", "after": f"j{j:02d}"}
        for i in range(18)
        for j in range(i + 1, 18)
    ][:101]
    assert len(edges) == 101
    body = {
        "jobs": [{"id": f"j{i:02d}", "family": "F"} for i in range(18)],
        "edges": edges,
    }
    _expect_422(body)


def test_edges_are_optional() -> None:
    resp = post(
        {"jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "X"}]}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["order"] == ["a", "b"]
    assert data["changeover_count"] == 0


# --------------------------------------------------------------------------
# immediate adjacency pairs
# --------------------------------------------------------------------------


def test_immediate_basic_success() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
            {"id": "c", "family": "X"},
        ],
        "edges": [],
        "immediate": [{"before": "a", "after": "c"}],
    }
    resp = post(body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "OK"
    assert data["order"] == ["a", "c", "b"]
    assert data["changeover_count"] == 1


def test_immediate_optional_empty_equals_omitted() -> None:
    base = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
        ],
        "edges": [{"before": "a", "after": "b"}],
    }
    omitted = post(base).json()
    empty = post({**base, "immediate": []}).json()
    assert omitted == empty


def test_unschedulable_response_has_no_partial_schedule() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
            {"id": "c", "family": "X"},
        ],
        "edges": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "c"},
        ],
        "immediate": [{"before": "a", "after": "c"}],
    }
    resp = post(body)
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "UNSCHEDULABLE"}


def test_self_immediate_pair_is_unschedulable_not_422() -> None:
    body = {
        "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
        "immediate": [{"before": "a", "after": "a"}],
    }
    resp = post(body)
    assert resp.status_code == 200
    assert resp.json() == {"status": "UNSCHEDULABLE"}


def test_cycle_with_immediate_still_cycle() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
            {"id": "c", "family": "Z"},
        ],
        "edges": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "c"},
            {"before": "c", "after": "a"},
        ],
        "immediate": [{"before": "a", "after": "b"}],
    }
    resp = post(body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CYCLE"
    assert data["cycle"] == ["a", "b", "c"]
    assert "order" not in data


def test_rejects_unknown_immediate_reference() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a", "after": "zzz"}],
        }
    )


def test_rejects_duplicate_immediate_pair() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
                {"id": "c", "family": "Z"},
            ],
            "immediate": [
                {"before": "a", "after": "b"},
                {"before": "a", "after": "b"},
            ],
        }
    )


def test_rejects_immediate_successor_fork() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
                {"id": "c", "family": "Z"},
            ],
            "immediate": [
                {"before": "a", "after": "b"},
                {"before": "a", "after": "c"},
            ],
        }
    )


def test_rejects_immediate_predecessor_fork() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
                {"id": "c", "family": "Z"},
            ],
            "immediate": [
                {"before": "a", "after": "c"},
                {"before": "b", "after": "c"},
            ],
        }
    )


def test_rejects_bad_immediate_pair_shape() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a"}],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a", "after": "b", "tight": True}],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": {},
        }
    )


def test_immediate_pair_does_not_share_duplicate_rule_with_edges() -> None:
    # The same ordered pair appearing once as an ordinary edge and once as an
    # immediate pair is legal (they express different requirements).
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
        ],
        "edges": [{"before": "a", "after": "b"}],
        "immediate": [{"before": "a", "after": "b"}],
    }
    resp = post(body)
    assert resp.status_code == 200
    assert resp.json()["order"] == ["a", "b"]


# --------------------------------------------------------------------------
# same-family run cap
# --------------------------------------------------------------------------


def test_run_cap_defaults_to_disabled() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
        ],
    }
    data = post(body).json()
    assert data["order"] == ["a", "b"]
    assert "family_runs" not in data


def test_run_cap_explicit_null_matches_omitted() -> None:
    base = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "Y"},
        ],
        "edges": [{"before": "a", "after": "b"}],
    }
    assert post(base).json() == post({**base, "max_same_family_run": None}).json()


def test_run_cap_success_payload() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "Y"},
            {"id": "d", "family": "X"},
        ],
        "max_same_family_run": 2,
    }
    data = post(body).json()
    assert data["status"] == "OK"
    assert data["order"] == ["a", "b", "c", "d"]
    assert data["family_runs"] == [
        {"family": "X", "start": 1, "end": 2, "length": 2, "jobs": ["a", "b"]},
        {"family": "Y", "start": 3, "end": 3, "length": 1, "jobs": ["c"]},
        {"family": "X", "start": 4, "end": 4, "length": 1, "jobs": ["d"]},
    ]
    # Every other payload field stays exactly where it was historically.
    assert data["changeover_count"] == 2
    assert data["changeover_positions"] == [3, 4]


def test_run_cap_unschedulable_has_no_partial_schedule() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
        ],
        "max_same_family_run": 1,
    }
    resp = post(body)
    assert resp.status_code == 200
    assert resp.json() == {"status": "UNSCHEDULABLE"}


def test_run_cap_in_chain_is_unschedulable_not_422() -> None:
    # A valid request whose fixed chain intrinsically violates the cap is a
    # scheduling impossibility, not a malformed request.
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "X"},
        ],
        "immediate": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "c"},
        ],
        "max_same_family_run": 2,
    }
    resp = post(body)
    assert resp.status_code == 200
    assert resp.json() == {"status": "UNSCHEDULABLE"}


def test_run_cap_cycle_still_cycle() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "X"},
        ],
        "edges": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "c"},
            {"before": "c", "after": "a"},
        ],
        "max_same_family_run": 1,
    }
    resp = post(body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CYCLE"
    assert data["cycle"] == ["a", "b", "c"]


def test_rejects_run_cap_zero() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
            ],
            "max_same_family_run": 0,
        }
    )


def test_rejects_run_cap_negative() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
            ],
            "max_same_family_run": -1,
        }
    )


def test_rejects_run_cap_above_job_limit() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
            ],
            "max_same_family_run": 19,
        }
    )


def test_rejects_run_cap_wrong_types() -> None:
    base_jobs = [
        {"id": "a", "family": "X"},
        {"id": "b", "family": "Y"},
    ]
    for bad in [True, "2", 2.0, [2]]:
        _expect_422({"jobs": base_jobs, "max_same_family_run": bad})
