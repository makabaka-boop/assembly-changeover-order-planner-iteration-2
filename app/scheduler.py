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


def solve(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]] | None = None,
) -> dict:
    """Compute the schedule payload.

    Returns one of::

        {"status": "CYCLE", "cycle": [...]}
        {"status": "UNSCHEDULABLE"}

        {"status": "OK", "order": [...], "changeover_count": k,
         "changeover_positions": [...], "changeovers": [...]}
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

    block_seq = _optimal_block_order(
        m,
        first_family,
        last_family,
        len(family_names),
        prereq_block,
        block_entry_changeovers,
    )

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

    return {
        "status": "OK",
        "order": order_ids,
        "changeover_count": len(positions),
        "changeover_positions": positions,
        "changeovers": details,
    }
