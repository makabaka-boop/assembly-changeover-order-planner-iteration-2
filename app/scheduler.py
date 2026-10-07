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

An optional cap on the length of any consecutive same-family run uses the
same block contraction but a state that also carries the current run
length: ``dp(mask, f, r)``.  A chain whose own fixed sequence already
contains an over-long run is rejected outright, while a violation that only
appears when two chains touch is detected by the transitions (a uniform
block extends the current run; a mixed block merges only its head run).
Both cases are therefore decided together with precedence and adjacency in
the one DP -- there is no unconstrained solve followed by a repair pass.
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
    chains: list[list[int]],
    family: list[int],
    num_families: int,
    cap: int,
    first_family: list[int],
    uniform_family: list[int],
    prereq_block: list[int],
) -> list[int] | None:
    """Like :func:`_optimal_block_order`, but no family may occupy more than
    *cap* consecutive orders.

    The state therefore has to distinguish not only the last placed block's
    tail family but also *how long* the current same-family run already is::

        dp(mask, f, r) = minimum remaining changeovers when the blocks in
                         *mask* are placed, the tail order has family *f*
                         and the trailing run of family *f* has length *r*.

    Two blocks of family *f* can meet only if their combined run length
    respects the cap; a block whose own fixed chain already contains a run
    longer than *cap* is rejected before the DP runs, but a violation that
    only arises when two chains are concatenated is discovered here -- hence
    solving the unconstrained optimum first and checking afterwards is
    impossible.

    Returns the block order, or ``None`` when no complete ordering exists.
    """
    # Per-block fixed runs of equal families, e.g. chain X,X,Y -> [(X,2),(Y,1)].
    block_runs: list[list[tuple[int, int]]] = []
    for ch in chains:
        runs: list[tuple[int, int]] = []
        cur_f = family[ch[0]]
        run_len = 1
        for p in range(1, len(ch)):
            f = family[ch[p]]
            if f == cur_f:
                run_len += 1
            else:
                runs.append((cur_f, run_len))
                cur_f = f
                run_len = 1
        runs.append((cur_f, run_len))
        block_runs.append(runs)
        if max(length for _, length in runs) > cap:
            # The chain itself violates the cap; no surrounding sequence can
            # split it because immediate pairs make it indivisible.
            return None

    # Number of orders of each family bounds the possible run lengths.
    family_count = [0] * num_families
    for f in family:
        family_count[f] += 1
    fam_limit = [min(cap, c) for c in family_count]

    # Flat slot layout: family f occupies slots
    # [slot_base[f], slot_base[f] + fam_limit[f]) encoding run length r as
    # slot_base[f] + (r - 1).  Accessing an impossible state yields INF.
    slot_base = [0] * num_families
    total_slots = 0
    for f in range(num_families):
        slot_base[f] = total_slots
        total_slots += fam_limit[f]

    size = 1 << m
    full = size - 1
    dp = bytearray([INF]) * (size * total_slots)
    # Nothing left to place: zero remaining cost from every reachable state.
    for s in range(total_slots):
        dp[full * total_slots + s] = 0

    allbits = full
    # Non-uniform blocks end in a fixed (family, run length) state regardless
    # of the predecessor run (the head-run merge is only a feasibility test).
    nonuniform_slot = [0] * m
    for b in range(m):
        if uniform_family[b] < 0:
            tail_f, tail_len = block_runs[b][-1]
            nonuniform_slot[b] = slot_base[tail_f] + (tail_len - 1)

    # Entry cost = number of changeovers fixed inside the block.
    entry_cost = [len(runs) - 1 for runs in block_runs]

    for mask in range(full - 1, -1, -1):
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

        base = mask * total_slots
        # Per family, the available blocks whose *head* has that family.
        # The successor-state DP value depends on the predecessor run r:
        #   * head family != f (one boundary changeover):
        #       uniform block length L -> state (hf, L)
        #       non-uniform block     -> its fixed tail state
        #   * head family == f (no boundary changeover), feasible only when
        #     the merged head run respects the cap:
        #       uniform length L       -> state (f, r + L)
        #       non-uniform head run k -> fixed tail state iff r + k <= cap
        other_costs: list[list[int]] = [[] for _ in range(num_families)]
        # (head run length, entry cost, successor dp value at fixed tail)
        same_nonuniform: list[list[tuple[int, int, int]]] = [
            [] for _ in range(num_families)
        ]
        same_uniform: list[list[int]] = [[] for _ in range(num_families)]
        candidates = avail
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            hf = first_family[b]
            succ_row = (mask | lb) * total_slots
            if uniform_family[b] >= 0:
                length = len(chains[b])
                nxt = dp[succ_row + slot_base[hf] + (length - 1)]
                if nxt < INF:
                    other_costs[hf].append(nxt + 1)
                    same_uniform[hf].append(b)
            else:
                nxt = dp[succ_row + nonuniform_slot[b]]
                if nxt < INF:
                    other_costs[hf].append(nxt + entry_cost[b] + 1)
                    same_nonuniform[hf].append(
                        (block_runs[b][0][1], entry_cost[b], nxt)
                    )
            candidates ^= lb

        # Smallest boundary cost per family, then the best cost coming from
        # *another* family (the runner-up must have a different head family).
        m1, m1_f, m2 = INF, -1, INF
        for f in range(num_families):
            best = min(other_costs[f], default=INF)
            if best < m1:
                m2 = m1
                m1, m1_f = best, f
            elif best < m2:
                m2 = best

        for f in range(num_families):
            limit = fam_limit[f]
            if limit == 0:
                continue
            sb = slot_base[f]
            other = m2 if f == m1_f else m1
            nonun = same_nonuniform[f]
            uni_blocks = same_uniform[f]
            for r in range(1, limit + 1):
                best = other
                # Non-uniform blocks starting in f: merge test, fixed tail.
                for head_len, ec, nxt in nonun:
                    if r + head_len <= cap and nxt + ec < best:
                        best = nxt + ec
                # Uniform blocks starting in f: the run extends to r + L.
                for b in uni_blocks:
                    new_r = r + len(chains[b])
                    if new_r <= limit:
                        nxt = dp[
                            (mask | (1 << b)) * total_slots + sb + (new_r - 1)
                        ]
                        if nxt < best:
                            best = nxt
                if best < INF:
                    dp[base + sb + (r - 1)] = best

    # Greedy reconstruction: blocks are indexed by first-job id byte order,
    # so ascending indices gives the lexicographically smallest optimum.
    order: list[int] = []
    mask = 0
    tail_f = -1
    tail_r = 0

    def successor_slot(b: int, preceding_f: int, preceding_r: int) -> int:
        """Slot of the state produced by appending block *b* after a run
        (preceding_f, preceding_r); -1 when it would violate the cap."""
        hf = first_family[b]
        if uniform_family[b] >= 0:
            length = len(chains[b])
            if preceding_f == hf:
                new_r = preceding_r + length
                if new_r > fam_limit[hf]:
                    return -1
                return slot_base[hf] + (new_r - 1)
            return slot_base[hf] + (length - 1)
        if (
            preceding_f == hf
            and preceding_r + block_runs[b][0][1] > fam_limit[hf]
        ):
            return -1
        return nonuniform_slot[b]

    while mask != full:
        avail = 0
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            if not (prereq_block[b] & ~mask):
                avail |= lb
            candidates ^= lb

        # Evaluate every available block in ascending index order.
        options: list[tuple[int, int]] = []  # (total cost incl. boundary, b)
        candidates = avail
        while candidates:
            lb = candidates & -candidates
            b = lb.bit_length() - 1
            succ_mask = mask | lb
            if mask == 0:
                # First block: there is no predecessor run at all, so never
                # any boundary changeover and never a concatenation check.
                if uniform_family[b] >= 0:
                    tail_slot = slot_base[first_family[b]] + (
                        len(chains[b]) - 1
                    )
                else:
                    tail_slot = nonuniform_slot[b]
                nxt = dp[succ_mask * total_slots + tail_slot]
                total = nxt + entry_cost[b] if nxt < INF else INF
            else:
                tail_slot = successor_slot(b, tail_f, tail_r)
                if tail_slot < 0:
                    total = INF
                else:
                    nxt = dp[succ_mask * total_slots + tail_slot]
                    if nxt >= INF:
                        total = INF
                    else:
                        total = nxt + entry_cost[b] + int(
                            first_family[b] != tail_f
                        )
            if total < INF:
                options.append((total, b))
            candidates ^= lb

        if not options:
            return None
        best_total = min(t for t, _b in options)
        # Blocks are indexed by first-job id byte order; candidates were
        # visited by low-bit order, so options is already in ascending index
        # order and the first hit gives the lexicographic tie-break.
        chosen = next(b for t, b in options if t == best_total)
        order.append(chosen)
        mask |= 1 << chosen
        hf = first_family[chosen]
        if mask.bit_count() == 1 or hf != tail_f:
            if uniform_family[chosen] >= 0:
                tail_f = hf
                tail_r = len(chains[chosen])
            else:
                tail_f, tail_r = block_runs[chosen][-1]
        else:
            # Same head family, uniform block: the run merges.  A non-uniform
            # block ending in the same family it starts with resets to its own
            # tail run length by construction (covered above by block_runs).
            if uniform_family[chosen] >= 0:
                tail_r += len(chains[chosen])
            else:
                tail_f, tail_r = block_runs[chosen][-1]

    return order


def solve(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]] | None = None,
    max_consecutive_same_family: int | None = None,
) -> dict:
    """Compute the schedule payload.

    Returns one of::

        {"status": "CYCLE", "cycle": [...]}
        {"status": "UNSCHEDULABLE"}

        {"status": "OK", "order": [...], "changeover_count": k,
         "changeover_positions": [...], "changeovers": [...]}

    When *max_consecutive_same_family* is set, the OK payload additionally
    carries ``max_consecutive_same_family`` (echoed) and ``family_runs`` (the
    maximal same-family segments of the order for review).
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
    # A block is "uniform" when its whole chain has one family: concatenating
    # it after another same-family block extends the current run, so the cap
    # test needs the combined length.  Mixed blocks always start a fresh run
    # whenever their head family differs, and their internal runs are fixed.
    uniform_family: list[int] = []
    for ch in chains:
        f0 = family[ch[0]]
        uniform_family.append(f0 if all(family[i] == f0 for i in ch) else -1)
    block_entry_changeovers = [
        sum(
            1 for p in range(1, len(ch)) if family[ch[p]] != family[ch[p - 1]]
        )
        for ch in chains
    ]

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

    if max_consecutive_same_family is None:
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
            chains,
            family,
            len(family_names),
            max_consecutive_same_family,
            first_family,
            uniform_family,
            prereq_block,
        )
        if block_seq is None:
            # The constraints are consistent (acyclic, chains intact) but no
            # complete ordering keeps every same-family run within the cap.
            return {"status": "UNSCHEDULABLE"}

    seq = [i for b in block_seq for i in chains[b]]

    order_ids = [ids[i] for i in seq]
    positions: list[int] = []
    details: list[dict] = []
    family_runs: list[dict] = []
    run_start = 1
    prev_f: str | None = None
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
            if max_consecutive_same_family is not None:
                family_runs.append(
                    {
                        "family": prev_f,
                        "start": run_start,
                        "end": pos - 1,
                        "length": pos - run_start,
                        "jobs": order_ids[run_start - 1 : pos - 1],
                    }
                )
            run_start = pos
        prev_f = f
    if max_consecutive_same_family is not None:
        n_ordered = len(order_ids)
        family_runs.append(
            {
                "family": prev_f,
                "start": run_start,
                "end": n_ordered,
                "length": n_ordered - run_start + 1,
                "jobs": order_ids[run_start - 1 :],
            }
        )

    payload = {
        "status": "OK",
        "order": order_ids,
        "changeover_count": len(positions),
        "changeover_positions": positions,
        "changeovers": details,
    }
    if max_consecutive_same_family is not None:
        # Echo the enforced cap and expose every maximal same-family segment
        # so the bound can be reviewed against the delivered order.
        payload["max_consecutive_same_family"] = max_consecutive_same_family
        payload["family_runs"] = family_runs
    return payload
