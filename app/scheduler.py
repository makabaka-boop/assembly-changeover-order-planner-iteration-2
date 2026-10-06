"""Exact low-changeover topological scheduler.

Given work orders (id, family), ordinary ``before -> after`` precedence
edges and optional *immediate* pairs demanding adjacent execution, find a
complete ordering that

1. satisfies every precedence edge and every immediate adjacency,
2. minimises the number of changeovers (positions where adjacent orders have
   different families; the first order never counts),
3. among all optimal orderings, is lexicographically smallest on the order's
   UTF-8 byte sequences.

Immediate pairs give each job at most one fixed predecessor and one fixed
successor; well-formed pairs therefore form disjoint chains.  The fixed
chains and the inter-chain precedence edges are decided *together*: every
chain is contracted into one indivisible block, ordinary precedence edges
become constraints between blocks (edges pointing "backwards" inside one
block are impossible), and the optimiser orders the blocks directly.  There
is no "solve first, move later" repair step.

The optimiser is a subset dynamic program over blocks::

    dp(mask, f) = minimum remaining changeovers when the blocks in *mask*
                  have already been placed and the family of the last placed
                  order (the tail family of the last block) is *f*.

With at most 18 orders there are at most 2**18 * 18 states, stored as a flat
``bytearray`` (max cost is 17, ``INF = 0x7F``).  The lexicographically
smallest optimum is then reconstructed greedily: blocks are tried in
ascending order of their first job id (UTF-8 byte order) and the first one
that can still attain the DP optimum is chosen.

An optional *same-family run cap* (``max_same_family_run``) forbids any
consecutive run of orders of one family from exceeding the cap.  The cap can
be violated inside a single fixed chain (then the instance is impossible no
matter how blocks are arranged) or only appear once two chains are stitched
together, so feasibility cannot be checked by repairing the unconstrained
optimum afterwards.  The capped solver therefore runs a second subset DP
whose state distinguishes both the tail family **and** the current run
length::

    dp(mask, f, r) = minimum remaining changeovers when *mask* has been
                     placed, the last order has family *f* and the trailing
                     same-family run has length *r*.

Placing a block that starts in family *f* either extends the trailing run
(legal only while its length stays within the cap) or crosses a boundary and
pays one changeover, resetting the run length.  The lex-smallest optimum is
reconstructed in exactly the same block order as the uncapped DP.
"""

from __future__ import annotations

from dataclasses import dataclass

INF = 0x7F  # larger than any real cost (at most n-1 <= 17)


@dataclass(frozen=True)
class Job:
    id: str
    family: str


def find_cycle(jobs: list[Job], edges: list[tuple[str, str]]) -> list[str] | None:
    """Return an actual directed cycle, or ``None`` if the graph is acyclic.

    The returned evidence is a list of node ids ``[v0, v1, ..., vk-1]`` such
    that every edge ``v_i -> v_{i+1}`` (and ``v_{k-1} -> v_0``) exists in the
    input.  Rotation starts at the smallest id (UTF-8 byte order); ties are
    broken by the remaining sequence.  Self loops yield ``[v]``.
    """
    order = sorted((j.id for j in jobs), key=lambda s: s.encode("utf-8"))
    index = {jid: i for i, jid in enumerate(order)}
    adj: list[list[int]] = [[] for _ in order]
    seen: set[tuple[int, int]] = set()
    for before, after in edges:
        u, v = index[before], index[after]
        if (u, v) not in seen:
            seen.add((u, v))
            adj[u].append(v)
    for row in adj:
        row.sort()

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * len(order)
    stack: list[int] = []
    on_stack: dict[int, int] = {}
    best: list[int] | None = None

    def rotate(cyc: list[int]) -> list[int]:
        start = min(range(len(cyc)), key=lambda i: order[cyc[i]].encode("utf-8"))
        return cyc[start:] + cyc[:start]

    def consider(cyc: list[int]) -> None:
        nonlocal best
        c = rotate(cyc)
        key = [order[v].encode("utf-8") for v in c]
        if best is None or key < [order[v].encode("utf-8") for v in best]:
            best = c

    def dfs(u: int) -> None:
        color[u] = GRAY
        on_stack[u] = len(stack)
        stack.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                consider(stack[on_stack[v]:])
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        del on_stack[u]
        color[u] = BLACK

    for start in range(len(order)):
        if color[start] == WHITE:
            dfs(start)

    if best is None:
        return None
    return [order[v] for v in best]


def _optimal_block_order(
    m: int,
    first_family: list[int],
    last_family: list[int],
    num_families: int,
    prereq_block: list[int],
    block_entry_changeovers: list[int],
) -> list[int]:
    """DP over subsets of immediate-chain blocks; returns block indices.

    Placing a block *b* after a placed order of family *f* costs
    ``block_entry_changeovers[b] + [first_family(b) != f]``: the fixed
    changeovers internal to the block, plus one boundary changeover unless
    the block starts in the same family as the order before it.
    """
    size = 1 << m
    full = size - 1
    # dp[mask * num_families + f]
    dp = bytearray([INF]) * (size * num_families)
    # No blocks left -> nothing remains to pay, for every tail family.
    for f in range(num_families):
        dp[full * num_families + f] = 0

    allbits = full

    for mask in range(full - 1, -1, -1):
        # Blocks that may be placed next: not yet placed, all prerequisites in.
        avail = 0
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            if not (prereq_block[b] & ~mask):
                avail |= lb
            candidates ^= lb
        if not avail:
            continue

        # best_by_fam[f] = min entry cost of b + dp(mask|{b}, last_family(b))
        # over available blocks b starting with family f.  The boundary
        # changeover against the (not yet known) preceding family is added
        # afterwards, outside the per-family minima.
        best_by_fam = [INF] * num_families
        candidates = avail
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            nxt = dp[(mask | lb) * num_families + last_family[b]]
            if nxt < INF:
                v = nxt + block_entry_changeovers[b]
                if v < best_by_fam[first_family[b]]:
                    best_by_fam[first_family[b]] = v
            candidates ^= lb
        m1 = INF  # smallest family minimum
        m1_f = -1
        m2 = INF  # smallest family minimum coming from another family
        for f, v in enumerate(best_by_fam):
            if v == INF:
                continue
            if v < m1:
                m2 = m1
                m1, m1_f = v, f
            elif v < m2:
                m2 = v

        # For each possible "last placed family" f:
        #   dp(mask, f) = min over available b of
        #       cost(b) + dp(mask|{b}, last_family(b))
        #                     + [first_family(b) != f]
        base = mask * num_families
        for f in range(num_families):
            same = best_by_fam[f]
            other = m2 if f == m1_f else m1
            if other == INF:
                # No available block starting in another family.
                dp[base + f] = same
            elif same == INF:
                dp[base + f] = other + 1
            else:
                dp[base + f] = same if same <= other + 1 else other + 1

    # Greedy reconstruction.  Blocks were built ordered by the UTF-8 byte
    # order of their first job id, so trying indices ascending yields the
    # lexicographically smallest job-id sequence among all optima.
    order: list[int] = []
    mask = 0
    last_f = -1
    remaining_cost: int | None = None
    while mask != full:
        avail = 0
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            if not (prereq_block[b] & ~mask):
                avail |= lb
            candidates ^= lb

        if mask == 0:
            # The first block never pays a boundary changeover, so its total
            # cost is its internal changeovers plus the successor state's
            # optimum.  Find that overall optimum, then pick the smallest
            # first-id block (blocks are indexed in that order) attaining it.
            best = INF
            candidates = avail
            while candidates:
                lb = candidates & -candidates
                b = lb.bit_length() - 1
                nxt = dp[lb * num_families + last_family[b]]
                if nxt < INF:
                    v = nxt + block_entry_changeovers[b]
                    if v < best:
                        best = v
                candidates ^= lb
            chosen = -1
            candidates = avail
            while candidates:
                lb = candidates & -candidates
                b = lb.bit_length() - 1
                nxt = dp[lb * num_families + last_family[b]]
                if (
                    nxt < INF
                    and nxt + block_entry_changeovers[b] == best
                ):
                    chosen = b
                    remaining_cost = nxt
                    break
                candidates ^= lb
        else:
            chosen = -1
            for b in range(m):
                if not (avail & (1 << b)):
                    continue
                nxt = dp[(mask | (1 << b)) * num_families + last_family[b]]
                edge_cost = int(first_family[b] != last_f)
                total = nxt + block_entry_changeovers[b] + edge_cost
                if total == remaining_cost:
                    chosen = b
                    # The successor state's optimal remaining value is nxt.
                    remaining_cost = nxt
                    break
        if chosen < 0:  # pragma: no cover - feasibility checked beforehand
            raise RuntimeError("DP reconstruction failed")
        order.append(chosen)
        mask |= 1 << chosen
        last_f = last_family[chosen]
    return order


def _optimal_block_order_capped(
    m: int,
    num_families: int,
    prereq_block: list[int],
    block_entry_changeovers: list[int],
    chain_runs: list[list[tuple[int, int]]],
    cap: int,
) -> list[int] | None:
    """Subset DP honouring the same-family run cap; returns block indices.

    Every block is described by the runs of equal families along its fixed
    chain: ``chain_runs[b]`` is a list of ``(family, length)`` pairs.  A block
    whose first run is longer than the cap can never be placed and makes the
    instance impossible; otherwise a state also records the trailing run
    length reached after the previous block.

    A block can be placed in two ways relative to the trailing family *f*:

    * it starts in family *f*: the trailing run merges with the block's first
      run (legal only while the merged length stays within the cap), no
      boundary changeover is paid;
    * it starts in another family: one boundary changeover is paid and the
      trailing state resets to the length of the block's last run.

    For a single-run block the merged run keeps growing (its successor state
    depends on the previous run length); a multi-run block ends in a run of
    its own last family, with a state-independent successor state.

    Returns ``None`` when no complete order exists.
    """
    lead = [runs[0][0] for runs in chain_runs]
    tail_fam = [runs[-1][0] for runs in chain_runs]
    tail_len = [runs[-1][1] for runs in chain_runs]
    lead_len = [runs[0][1] for runs in chain_runs]
    single_run = [len(runs) == 1 for runs in chain_runs]
    entry_cost = block_entry_changeovers

    # States: dp[(mask * num_families + f) * (cap + 1) + r] with 1 <= r <= cap.
    width = cap + 1
    layers = num_families * width
    size = 1 << m
    full = size - 1
    dp = bytearray([INF]) * (size * layers)
    full_base = full * layers
    for f in range(num_families):
        slot = full_base + f * width
        for r in range(1, width):
            dp[slot + r] = 0

    # job_count[mask * num_families + f] = number of f-family jobs contained
    # in the blocks of mask, capped at *cap* (stored in a byte; cap <= 18).
    # A trailing run of family f after mask cannot exceed this, so the fill
    # below only iterates reachable r values -- important when there are many
    # families but few jobs of each (most (f, r) slots are otherwise dead).
    block_family_count = [[0] * num_families for _ in range(m)]
    for b, runs in enumerate(chain_runs):
        for f, length in runs:
            block_family_count[b][f] += length
    job_count = bytearray(size * num_families)
    for mask in range(1, size):
        lb = mask & -mask
        b = lb.bit_length() - 1
        prev = (mask ^ lb) * num_families
        cur = mask * num_families
        counts_b = block_family_count[b]
        for f in range(num_families):
            c = job_count[prev + f] + counts_b[f]
            job_count[cur + f] = c if c < cap else cap

    allbits = full

    for mask in range(full - 1, -1, -1):
        avail: list[int] = []
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            if not (prereq_block[b] & ~mask):
                avail.append(b)
            candidates ^= lb
        if not avail:
            continue

        # q[b] is the state-independent value reached by placing b with a
        # paid boundary (or as the very first block): internal changeovers
        # plus the optimum at the successor state (b's own last run).
        q: list[int] = [INF] * m
        placed_base: list[int] = [0] * m
        for b in avail:
            placed_base[b] = (mask | (1 << b)) * layers
            nxt = dp[placed_base[b] + tail_fam[b] * width + tail_len[b]]
            if nxt < INF:
                q[b] = nxt + entry_cost[b]

        # Cross-family transition: one boundary changeover plus the smallest
        # q over blocks starting in *another* lead family.  Aggregate per
        # lead family first (several blocks may share a family), then keep
        # the smallest and second-smallest family minima.
        fam_best = [INF] * num_families
        # Extend transitions, grouped by lead family:
        #   multi[(length, q)]: multi-run blocks have a state-independent
        #     successor state (the block's own last run);
        #   single blocks need dp(mask|b, f, r + length), which depends on r,
        #     so they must be considered even when q[b] is INF (the reset
        #     state may be unreachable while an extended state is not).
        multi: list[list[tuple[int, int]]] = [[] for _ in range(num_families)]
        singles: list[list[int]] = [[] for _ in range(num_families)]
        for b in avail:
            g = lead[b]
            if single_run[b]:
                singles[g].append(b)
            if q[b] < fam_best[g]:
                fam_best[g] = q[b]
            if q[b] < INF and not single_run[b]:
                multi[g].append((lead_len[b], q[b]))
        m1 = INF
        m1_g = -1
        m2 = INF
        for g, v in enumerate(fam_best):
            if v == INF:
                continue
            if v < m1:
                m2 = m1
                m1, m1_g = v, g
            elif v < m2:
                m2 = v

        # threshold_best[g][t] = smallest q of a multi-run block lead=g whose
        # first run has length <= t.
        threshold_best: list[list[int]] = []
        for g in range(num_families):
            row = [INF] * width
            if multi[g]:
                for length, v in multi[g]:
                    if v < row[length]:
                        row[length] = v
                best = INF
                for t in range(width):
                    if row[t] < best:
                        best = row[t]
                    row[t] = best
            threshold_best.append(row)

        base = mask * layers
        jc_base = mask * num_families
        for f in range(num_families):
            # Only run lengths that the placed jobs can actually reach.
            r_limit = job_count[jc_base + f]
            if r_limit == 0:
                continue
            cross = (m2 if f == m1_g else m1)
            cross_v = cross + 1 if cross < INF else INF
            thresh = threshold_best[f]
            f_singles = singles[f]
            slot = base + f * width
            for r in range(1, r_limit + 1):
                best = cross_v
                t = cap - r
                if t > 0:
                    v = thresh[t]
                    if v < best:
                        best = v
                    # Single-run blocks: the merged run reaches r + length,
                    # so the successor state's run length depends on r.
                    for b in f_singles:
                        length = lead_len[b]
                        if length > t:
                            continue
                        val = dp[
                            placed_base[b] + f * width + r + length
                        ]
                        if val < best:
                            best = val
                if best < INF:
                    dp[slot + r] = best

    # Greedy reconstruction: blocks are indexed by ascending first-job-id
    # byte order, so the first block that can still attain the optimum yields
    # the lexicographically smallest job-id sequence.
    total = INF
    for b in range(m):
        if prereq_block[b] != 0:
            continue
        nxt = dp[(1 << b) * layers + tail_fam[b] * width + tail_len[b]]
        if nxt < INF:
            v = nxt + entry_cost[b]
            if v < total:
                total = v
    if total >= INF:
        return None

    order: list[int] = []
    mask = 0
    last_f = -1
    last_r = 0
    remaining = total
    while mask != full:
        chosen = -1
        for b in range(m):
            if mask & (1 << b):
                continue
            if prereq_block[b] & ~mask:
                continue
            if mask == 0:
                nf, nr = tail_fam[b], tail_len[b]
                cost = entry_cost[b]
            elif lead[b] == last_f:
                merged = last_r + lead_len[b]
                if merged > cap:
                    continue
                cost = entry_cost[b]
                if single_run[b]:
                    nf, nr = last_f, merged
                else:
                    nf, nr = tail_fam[b], tail_len[b]
            else:
                nf, nr = tail_fam[b], tail_len[b]
                cost = entry_cost[b] + 1
            nxt = dp[(mask | (1 << b)) * layers + nf * width + nr]
            if nxt < INF and nxt + cost == remaining:
                chosen = b
                remaining = nxt
                last_f, last_r = nf, nr
                break
        if chosen < 0:  # pragma: no cover - reachable states stay reachable
            return None
        order.append(chosen)
        mask |= 1 << chosen
    return order


def solve(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]] | None = None,
    max_same_family_run: int | None = None,
) -> dict:
    """Compute the schedule payload.

    Returns one of::

        {"status": "CYCLE", "cycle": [...]}
        {"status": "UNSCHEDULABLE"}

        {"status": "OK", "order": [...], "changeover_count": k,
         "changeover_positions": [...], "changeovers": [...]}

    When *max_same_family_run* is an integer, every maximal consecutive run
    of orders of one recipe family in the result has at most that length;
    otherwise the result is unachievable and ``UNSCHEDULABLE`` is returned.
    The ordinary precedence graph is still checked for a cycle first, so the
    CYCLE verdict keeps its original precedence.  When the cap is enabled the
    payload additionally carries ``family_runs`` (the runs of the result);
    when it is omitted, the payload is unchanged field by field.
    """
    cycle = find_cycle(jobs, edges)
    if cycle is not None:
        return {"status": "CYCLE", "cycle": cycle}

    ids = sorted((j.id for j in jobs), key=lambda s: s.encode("utf-8"))
    idx = {jid: i for i, jid in enumerate(ids)}
    n = len(ids)

    family_names = sorted({j.family for j in jobs}, key=lambda s: s.encode("utf-8"))
    fam_idx = {f: i for i, f in enumerate(family_names)}
    family_of_id = {j.id: j.family for j in jobs}
    family = [fam_idx[family_of_id[jid]] for jid in ids]

    immediate = immediate or []

    # Contract immediate pairs into fixed chains (blocks).  The request
    # validator already guarantees in-degree <= 1, out-degree <= 1 and no
    # duplicate pairs, so walking from every predecessor-free head either
    # covers every job (disjoint chains) or finds an immediate-only cycle,
    # including a self pair a -> a: no linear order can satisfy it.
    succ: dict[str, str] = {}
    has_pred: set[str] = set()
    for before, after in immediate:
        succ[before] = after
        has_pred.add(after)

    chains: list[list[int]] = []
    visited = [False] * n
    for head in ids:
        if head in has_pred:
            continue
        chain: list[int] = []
        cur: str | None = head
        while cur is not None:
            i = idx[cur]
            if visited[i]:  # pragma: no cover - excluded by validation
                break
            visited[i] = True
            chain.append(i)
            cur = succ.get(cur)
        chains.append(chain)
    if not all(visited):
        return {"status": "UNSCHEDULABLE"}

    # Order blocks by the byte order of their first job id, so that the DP
    # reconstruction's ascending-index tie-break gives the lex-smallest
    # sequence of job ids.
    chains.sort(key=lambda ch: ids[ch[0]].encode("utf-8"))
    m = len(chains)
    block_of = [-1] * n
    pos_in_block = [-1] * n
    for b, chain in enumerate(chains):
        for p, i in enumerate(chain):
            block_of[i] = b
            pos_in_block[i] = p

    first_family = [family[ch[0]] for ch in chains]
    last_family = [family[ch[-1]] for ch in chains]
    block_entry_changeovers = [
        sum(
            1 for p in range(1, len(ch)) if family[ch[p]] != family[ch[p - 1]]
        )
        for ch in chains
    ]

    # Runs of equal families inside each fixed chain.  With the run cap on, a
    # chain that already overflows the cap internally can never be part of a
    # legal order -- not even at its very start -- and neither can a violation
    # be repaired by reordering (the chain is indivisible).
    chain_runs: list[list[tuple[int, int]]] = []
    for ch in chains:
        runs: list[tuple[int, int]] = []
        for i in ch:
            if runs and runs[-1][0] == family[i]:
                runs[-1] = (family[i], runs[-1][1] + 1)
            else:
                runs.append((family[i], 1))
        chain_runs.append(runs)
    if max_same_family_run is not None and any(
        length > max_same_family_run for runs in chain_runs for _, length in runs
    ):
        return {"status": "UNSCHEDULABLE"}

    # Turn ordinary precedence edges into block-level constraints.  An edge
    # inside one block is satisfiable only when it follows the fixed chain
    # direction; edges between blocks mean the source block must precede the
    # target block.  A cycle among blocks cannot exist (the ordinary graph is
    # a DAG) unless the immediate chains force one.
    prereq_block = [0] * m
    block_adj: list[list[int]] = [[] for _ in range(m)]
    for before, after in edges:
        u, v = idx[before], idx[after]
        bu, bv = block_of[u], block_of[v]
        if bu == bv:
            if pos_in_block[u] >= pos_in_block[v]:
                return {"status": "UNSCHEDULABLE"}
            continue
        bit = 1 << bu
        if not (prereq_block[bv] & bit):
            prereq_block[bv] |= bit
            block_adj[bu].append(bv)

    # Kahn's algorithm on the block graph: if it stalls, the immediate
    # chains together with the precedence edges are contradictory.
    indegree = [0] * m
    for u in range(m):
        for v in block_adj[u]:
            indegree[v] += 1
    ready = [b for b in range(m) if indegree[b] == 0]
    seen_count = 0
    while ready:
        u = ready.pop()
        seen_count += 1
        for v in block_adj[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                ready.append(v)
    if seen_count != m:
        return {"status": "UNSCHEDULABLE"}

    if max_same_family_run is None:
        block_seq = _optimal_block_order(
            m,
            first_family,
            last_family,
            len(family_names),
            prereq_block,
            block_entry_changeovers,
        )
    else:
        block_seq = _optimal_block_order_capped(
            m,
            len(family_names),
            prereq_block,
            block_entry_changeovers,
            chain_runs,
            max_same_family_run,
        )
        if block_seq is None:
            return {"status": "UNSCHEDULABLE"}

    seq = [i for b in block_seq for i in chains[b]]

    order_ids = [ids[i] for i in seq]
    positions: list[int] = []
    details: list[dict] = []
    prev_f = None
    for pos, jid in enumerate(order_ids, start=1):
        f = family_of_id[jid]
        if prev_f is not None and f != prev_f:
            positions.append(pos)
            details.append(
                {
                    "position": pos,
                    "from": {"id": order_ids[pos - 2], "family": prev_f},
                    "to": {"id": jid, "family": f},
                }
            )
        prev_f = f

    payload = {
        "status": "OK",
        "order": order_ids,
        "changeover_count": len(positions),
        "changeover_positions": positions,
        "changeovers": details,
    }
    if max_same_family_run is not None:
        # Maximal consecutive same-family runs of the delivered order, for
        # auditing the cap.  Each segment carries its 1-based [start, end]
        # positions, length, family and the job ids making up the run.
        runs_out: list[dict] = []
        start = 0
        while start < n:
            end = start + 1
            f = family_of_id[order_ids[start]]
            while end < n and family_of_id[order_ids[end]] == f:
                end += 1
            runs_out.append(
                {
                    "family": f,
                    "start": start + 1,
                    "end": end,
                    "length": end - start,
                    "jobs": order_ids[start:end],
                }
            )
            start = end
        payload["family_runs"] = runs_out
    return payload
