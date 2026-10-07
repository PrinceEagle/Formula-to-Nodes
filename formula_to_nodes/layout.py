# SPDX-License-Identifier: GPL-3.0-or-later
"""Tidy left-to-right layout for generated node trees. Pure Python.

* Columns: a node's column is its longest distance to a sink, so every link
  flows left → right and inputs sit right next to what consumes them.
* Zones: nodes outside a zone that feed into it are pushed left of the zone's
  input node, so nothing outside a zone is drawn inside its frame.
* Rows: a few barycenter sweeps pull each node toward the nodes it connects
  to, then overlaps are resolved per column.
"""

from statistics import fmean


def _reach(start, adj):
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def compute(nodes, edges, zones=(), gap_x=70.0, gap_y=30.0):
    """nodes: {id: (width, height)} in insertion order; edges: [(src, dst)];
    zones: [(zone_input_id, zone_output_id)].
    Returns {id: (x, y)} as Blender locations (top-left corner, y up)."""
    ids = list(nodes)
    if not ids:
        return {}
    order_index = {u: i for i, u in enumerate(ids)}
    succ = {u: [] for u in ids}
    pred = {u: [] for u in ids}
    for a, b in edges:
        if a in succ and b in succ and a != b and b not in succ[a]:
            succ[a].append(b)
            pred[b].append(a)

    # topological order (Kahn); generated trees are DAGs
    indeg = {u: len(pred[u]) for u in ids}
    topo = [u for u in ids if indeg[u] == 0]
    k = 0
    while k < len(topo):
        u = topo[k]
        k += 1
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                topo.append(v)
    if len(topo) < len(ids):
        placed = set(topo)
        topo += [u for u in ids if u not in placed]

    rank = {}
    for u in reversed(topo):
        rank[u] = 1 + max((rank.get(v, 0) for v in succ[u]), default=-1)

    def bump(u, r):
        stack = [(u, r)]
        while stack:
            x, rr = stack.pop()
            if rank[x] >= rr:
                continue
            rank[x] = rr
            for p in pred[x]:
                stack.append((p, rr + 1))

    zones = [(zi, zo) for zi, zo in zones if zi in succ and zo in succ]
    for _ in range(8):
        changed = False
        for zi, zo in zones:
            inside = (_reach(zi, succ) & _reach(zo, pred)) | {zi, zo}
            for v in inside:
                if v == zi:
                    continue
                for u in pred[v]:
                    if u not in inside and rank[u] <= rank[zi]:
                        bump(u, rank[zi] + 1)
                        changed = True
        if not changed:
            break

    max_rank = max(rank.values())
    cols = [[] for _ in range(max_rank + 1)]
    for u in ids:
        cols[rank[u]].append(u)
    widths = [max((nodes[u][0] for u in col), default=0.0) for col in cols]

    x_left, cursor = [], 0.0
    for r in range(max_rank + 1):
        x_left.append(cursor - widths[r])
        cursor = x_left[r] - gap_x
    px = {u: x_left[rank[u]] + widths[rank[u]] - nodes[u][0] for u in ids}

    center = {}

    def place(col, desired):
        if not col:
            return
        items = sorted(col, key=lambda u: (desired[u], order_index[u]))
        tops, cur = [], float("-inf")
        for u in items:
            h = nodes[u][1]
            top = max(desired[u] - h / 2.0, cur)
            tops.append(top)
            cur = top + h + gap_y
        drift = fmean(desired[u] - (t + nodes[u][1] / 2.0) for u, t in zip(items, tops))
        for u, t in zip(items, tops):
            center[u] = t + drift + nodes[u][1] / 2.0

    y = 0.0
    first = {}
    for u in cols[0]:
        first[u] = y + nodes[u][1] / 2.0
        y += nodes[u][1] + gap_y
    place(cols[0], first)
    for r in range(1, max_rank + 1):
        want = {}
        for i, u in enumerate(cols[r]):
            ys = [center[v] for v in succ[u] if v in center]
            want[u] = fmean(ys) if ys else i * 150.0
        place(cols[r], want)

    for _ in range(3):
        for r in range(max_rank - 1, -1, -1):
            want = {}
            for u in cols[r]:
                ys = [center[p] for p in pred[u]] + [center[v] for v in succ[u]]
                want[u] = fmean(ys) * 0.5 + center[u] * 0.5 if ys else center[u]
            place(cols[r], want)
        for r in range(1, max_rank + 1):
            want = {}
            for u in cols[r]:
                ys = [center[v] for v in succ[u]] + [center[p] for p in pred[u]]
                want[u] = fmean(ys) * 0.5 + center[u] * 0.5 if ys else center[u]
            place(cols[r], want)

    # keep zone input/output roughly level so the zone frame reads cleanly
    for zi, zo in zones:
        mid = (center[zi] + center[zo]) / 2.0
        for u in (zi, zo):
            col = cols[rank[u]]
            want = {v: center[v] for v in col}
            want[u] = mid
            place(col, want)

    top_all = min(center[u] - nodes[u][1] / 2.0 for u in ids)
    return {u: (px[u], -(center[u] - nodes[u][1] / 2.0 - top_all)) for u in ids}
