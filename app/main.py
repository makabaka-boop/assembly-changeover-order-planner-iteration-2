"""FastAPI application: exact low-changeover work-order scheduling."""

from __future__ import annotations

from fastapi import FastAPI

from .scheduler import Job, solve
from .schemas import ScheduleRequest

app = FastAPI(
    title="Low-Changeover Assembly Line Scheduler",
    version="1.0.0",
    description=(
        "Orders work orders so that all precedence edges and immediate "
        "adjacency pairs are satisfied, the number of recipe-family "
        "changeovers is minimised, and the result is lexicographically "
        "smallest (UTF-8 byte order)."
    ),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/schedule")
def schedule(request: ScheduleRequest) -> dict:
    jobs = [Job(id=j.id, family=j.family) for j in request.jobs]
    edges = [(e.before, e.after) for e in request.edges]
    immediate = [(p.before, p.after) for p in request.immediate]
    return solve(jobs, edges, immediate)
