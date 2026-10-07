"""Request schemas with strict 422 validation."""

from __future__ import annotations

import pydantic
from pydantic import BaseModel, Field, model_validator

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
    # Optional cap on how many orders of one recipe family may run back to
    # back.  ``None`` (omitted or null) disables the limit; an out-of-range
    # or wrong-typed value rejects the whole request with 422.
    max_consecutive_same_family: int | None = Field(default=None)

    @pydantic.field_validator("max_consecutive_same_family", mode="before")
    @classmethod
    def _validate_consecutive_cap(cls, v: object) -> int | None:
        if v is None:
            return None
        # Pydantic in lax mode would coerce floats such as ``1.0``, bools and
        # even numeric strings into ints; the field contract is strict: an
        # actual JSON integer (or null).  ``bool`` is an ``int`` subclass, so
        # check it first.
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("max_consecutive_same_family must be an integer")
        if v < 1:
            raise ValueError("max_consecutive_same_family must be at least 1")
        if v > MAX_JOBS:
            raise ValueError(
                f"max_consecutive_same_family must be at most {MAX_JOBS}"
            )
        return v

    @model_validator(mode="after")
    def _validate(self) -> "ScheduleRequest":
        ids = [j.id for j in self.jobs]
        if len(set(ids)) != len(ids):
            raise ValueError("job ids must be unique")

        # The cap may not exceed the number of orders in this request: such a
        # value could never bind and is an illegal range for the instance.
        cap = self.max_consecutive_same_family
        if cap is not None and cap > len(ids):
            raise ValueError(
                "max_consecutive_same_family must not exceed the number of jobs"
            )

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
