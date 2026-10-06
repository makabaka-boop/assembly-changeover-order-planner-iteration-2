"""Request schemas with strict 422 validation."""

from __future__ import annotations

import pydantic
from pydantic import BaseModel, Field, field_validator, model_validator

MAX_JOBS = 18
MIN_JOBS = 2
MAX_EDGES = 100


def _ascii_nonempty(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) == 0:
        raise ValueError(f"{name} must be non-empty")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must contain ASCII characters only")
    return value


class JobIn(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    id: str
    family: str

    @pydantic.field_validator("id", "family")
    @classmethod
    def _ascii_nonempty(cls, v: str, info: pydantic.ValidationInfo) -> str:
        return _ascii_nonempty(v, info.field_name)


class EdgeIn(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    before: str
    after: str

    @pydantic.field_validator("before", "after")
    @classmethod
    def _ascii_nonempty(cls, v: str, info: pydantic.ValidationInfo) -> str:
        return _ascii_nonempty(v, info.field_name)


class ImmediateIn(EdgeIn):
    """Directed pair demanding adjacent execution (``before`` right before ``after``).

    Degree limits, unknown references and duplicates are validated on the
    request; a self loop is left to the solver, which reports
    ``UNSCHEDULABLE`` (no linear order can place a job adjacent to itself).
    """


class ScheduleRequest(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    jobs: list[JobIn] = Field(..., min_length=MIN_JOBS, max_length=MAX_JOBS)
    edges: list[EdgeIn] = Field(default_factory=list)
    immediate: list[ImmediateIn] = Field(default_factory=list)
    # When set, no run of consecutive orders sharing one recipe family may be
    # longer than this value.  ``None`` (or omitted) disables the limit and
    # keeps the historical response unchanged.  Booleans are rejected on
    # purpose even though ``bool`` is a subclass of ``int`` in Python.
    max_same_family_run: int | None = Field(default=None)

    @field_validator("max_same_family_run", mode="before")
    @classmethod
    def _validate_run_cap(cls, v: object) -> object:
        if v is None:
            return None
        # Inspect the raw value before pydantic coercion: booleans are ints in
        # Python, and lax int parsing would accept "2" and 2.0 otherwise.
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("max_same_family_run must be an integer")
        if not (1 <= v <= MAX_JOBS):
            raise ValueError(
                f"max_same_family_run must be between 1 and {MAX_JOBS}"
            )
        return v

    @model_validator(mode="after")
    def _validate(self) -> "ScheduleRequest":
        ids = [j.id for j in self.jobs]
        if len(set(ids)) != len(ids):
            raise ValueError("job ids must be unique")

        raw_edges = [(e.before, e.after) for e in self.edges]
        if len(raw_edges) > MAX_EDGES:
            raise ValueError(f"at most {MAX_EDGES} precedence edges are allowed")

        known = set(ids)
        seen: set[tuple[str, str]] = set()
        for before, after in raw_edges:
            if before not in known or after not in known:
                raise ValueError("edge references unknown job id")
            if before == after:
                raise ValueError("self loops are not allowed")
            if (before, after) in seen:
                raise ValueError("duplicate edge")
            seen.add((before, after))

        raw_immediate = [(p.before, p.after) for p in self.immediate]
        seen_immediate: set[tuple[str, str]] = set()
        has_predecessor: set[str] = set()
        has_successor: set[str] = set()
        for before, after in raw_immediate:
            if before not in known or after not in known:
                raise ValueError("immediate pair references unknown job id")
            if (before, after) in seen_immediate:
                raise ValueError("duplicate immediate pair")
            if before in has_successor:
                raise ValueError("job has more than one immediate successor")
            if after in has_predecessor:
                raise ValueError("job has more than one immediate predecessor")
            seen_immediate.add((before, after))
            has_successor.add(before)
            has_predecessor.add(after)
        return self
