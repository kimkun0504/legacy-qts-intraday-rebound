"""Entry and exit variants on minute bars, one pass per order (numba).

Entries (all cancelled at the cut-off; L_k = prior close x (1 - k sigma), as traded):
  lim  k      resting buy limit at L_k: the stage-2 fill model (auction part, then 0.5 % trade-through
              plus 2x the order's volume at or below the limit).
  auc  k      limit-on-open at L_k: only the auction part (at most 25 % of the first minute's volume).
  vol  k, m   after a bar trades at or below L_k with volume >= m x the average minute volume
              (63-day ADV / 390), buy the next bar's open (market order).
  rev  k, n   after L_k has traded, the first bar that closes above the high of the previous n
              bars; buy the next bar's open.
  time k, t   at clock minute t, if the last trade is at or below L_k, buy the next bar's open.
Market entries need the entry bar to trade at least 2x the order; ``stress_px`` is that bar's high.

Exits (from the entry on; otherwise the official close, MOC):
  moc | tgt x (sell limit, needs a 0.5 % trade-through) | stop y (stop market: min(stop, open)
  less 0.5 %) | time N minutes (next bar's open) | tgt x + stop y (stop first within a bar).
"""

from __future__ import annotations

import numpy as np
from numba import njit

OPEN = 570
POSITION = 500.0

ENTRIES: list[tuple] = (
    [("lim", k, 0) for k in (2, 3, 4, 5, 6, 8)]
    + [("auc", k, 0) for k in (2, 3, 4, 6)]
    + [("vol", k, m) for k in (2, 4) for m in (5, 20, 50)]
    + [("rev", k, n) for k in (2, 4) for n in (3, 10)]
    + [("time", k, t) for k in (2, 4) for t in (600, 720, 840, 930)]
    + [("vol", k, 0) for k in (2, 3, 4)]  # reactive: the first bar that trades at or below L_k
    + [("rlim", k, 0) for k in (2, 3)]  # reactive limit: rest at L_k only after it first trades
    # the same with a floor on the drop: L = prior close x (1 - max(k sigma, x))
    + [("lim", 2, 0, x) for x in (0.05, 0.08, 0.12)]
    + [("rlim", 2, 0, x) for x in (0.05, 0.08)]
    + [("vol", 2, 0, x) for x in (0.05, 0.08)]
    # stricter fills for the resting limit: trade-through p (instead of 0.5 %) and 5x the volume
    + [("lim", 2, p, 0.05) for p in (0.02, 0.05)]
)
EXITS: list[tuple[str, float, float]] = (
    [("moc", 0, 0)]
    + [("tgt", x, 0) for x in (0.02, 0.03, 0.05, 0.08, 0.12)]
    + [("stop", 0, y) for y in (0.03, 0.05, 0.10)]
    + [("time", n, 0) for n in (15, 60, 120)]
    + [("tgtstop", x, y) for x, y in ((0.05, 0.05), (0.08, 0.05), (0.03, 0.03), (0.12, 0.10))]
)
ETYPE = {"lim": 0, "auc": 1, "vol": 2, "rev": 3, "time": 4, "rlim": 5}
XTYPE = {"moc": 0, "tgt": 1, "stop": 2, "time": 3, "tgtstop": 4}


def entry_name(e: tuple) -> str:
    t, k, p = e[:3]
    base = f"{t}{k:g}" if t in ("auc", "rlim") or (t == "lim" and not p) else f"{t}{k:g}_{p:g}"
    return base + (f"_abs{e[3] * 100:g}" if len(e) > 3 else "")


def exit_name(x: tuple[str, float, float]) -> str:
    t, a, b = x
    if t == "moc":
        return "moc"
    if t == "tgt":
        return f"tgt{a * 100:g}"
    if t == "stop":
        return f"stop{b * 100:g}"
    if t == "time":
        return f"t{a:g}"
    return f"tgt{a * 100:g}_stop{b * 100:g}"


N_FEAT = 8  # relvol, gap_sig, depth_sig, bars_before, first_bar_rel, trig_rel, mfe, mae


@njit(cache=True)
def kernel(
    position,
    off,
    minute,
    o,
    h,
    l,
    c,
    v,
    cprev,
    sigma,
    cu,
    cutoff,
    close,
    advsh,
    etype,
    ek,
    ep,
    eabs,
    xtype,
    xa,
    xb,
    out_oid,
    out_e,
    out_min,
    out_px,
    out_stress,
    out_sh,
    out_qa,
    out_feat,
    out_ret,
):
    n_o = len(cprev)
    n_e = len(etype)
    n_x = len(xtype)
    r = 0
    for i in range(n_o):
        a = off[i]
        b = off[i + 1]
        if b <= a or not (cprev[i] > 0) or not (sigma[i] > 0) or not (cu[i] > 0):
            continue
        # bars usable for exits: minute < close
        nb = a
        while nb < b and minute[nb] < close[i]:
            nb += 1
        if nb <= a:
            continue
        avgmin = advsh[i] / 390.0
        for e in range(n_e):
            L = cprev[i] * (1.0 - max(ek[e] * sigma[i], eabs[e]))
            if not (L > 0):
                continue
            q = max(np.floor(position / L), 1.0)
            start = -1
            px = 0.0
            stress = 0.0
            sh = 0.0
            qa = 0.0
            emin = 0
            trig = -1
            t = etype[e]
            if t == 0 or t == 1:
                if minute[a] == OPEN and o[a] <= L:
                    qa = min(q, np.floor(0.25 * v[a]))
                need = q - qa if t == 0 else 0.0
                thr = ep[e] if ep[e] > 0 else 0.005
                vmult = 5.0 if ep[e] > 0 else 2.0
                contj = -1
                if need > 0:
                    seen = False
                    cum = 0.0
                    for j in range(a, nb):
                        if minute[j] >= cutoff[i]:
                            break
                        if l[j] <= L:
                            cum += v[j]
                            if l[j] <= L * (1.0 - thr):
                                seen = True
                            if seen and cum >= vmult * need:
                                contj = j
                                break
                qc = need if contj >= 0 else 0.0
                sh = qa + qc
                if sh <= 0:
                    continue
                px = (qa * o[a] + qc * L) / sh
                stress = px
                if contj >= 0:
                    start = contj + 1
                    emin = minute[contj] + 1
                    trig = contj
                else:
                    start = a
                    emin = OPEN
                    trig = a
            elif t == 5:
                trigj = -1
                for j in range(a, nb):
                    if minute[j] >= cutoff[i]:
                        break
                    if l[j] <= L:
                        trigj = j
                        break
                if trigj < 0:
                    continue
                contj = -1
                seen = False
                cum = 0.0
                for j in range(trigj + 1, nb):
                    if minute[j] >= cutoff[i]:
                        break
                    if l[j] <= L:
                        cum += v[j]
                        if l[j] <= L * 0.995:
                            seen = True
                        if seen and cum >= 2.0 * q:
                            contj = j
                            break
                if contj < 0:
                    continue
                sh = q
                px = L
                stress = L
                start = contj + 1
                emin = minute[contj] + 1
                trig = trigj
            else:
                j0 = -1
                if t == 2:
                    for j in range(a, nb):
                        if minute[j] >= cutoff[i]:
                            break
                        if l[j] <= L and v[j] >= ep[e] * avgmin and v[j] > 0:
                            j0 = j
                            break
                elif t == 3:
                    n = int(ep[e])
                    seen = False
                    for j in range(a, nb):
                        if minute[j] >= cutoff[i]:
                            break
                        if l[j] <= L:
                            seen = True
                        if seen and j - a >= n:
                            hh = h[j - 1]
                            for u in range(j - n, j - 1):
                                if h[u] > hh:
                                    hh = h[u]
                            if c[j] > hh:
                                j0 = j
                                break
                else:
                    last = -1
                    for j in range(a, nb):
                        if minute[j] >= ep[e]:
                            break
                        last = j
                    if last >= 0 and c[last] <= L:
                        j0 = last
                if j0 < 0 or j0 + 1 >= nb or minute[j0 + 1] >= cutoff[i]:
                    continue
                j1 = j0 + 1
                if not (o[j1] > 0):
                    continue
                q2 = max(np.floor(position / o[j1]), 1.0)
                if v[j1] < 2.0 * q2:
                    continue
                sh = q2
                px = o[j1]
                stress = h[j1]
                start = j1
                emin = minute[j1]
                trig = j0
            # features
            cumv = 0.0
            for j in range(a, start):
                cumv += v[j]
            out_feat[r, 0] = cumv / advsh[i] if advsh[i] > 0 else np.nan
            out_feat[r, 1] = (o[a] / cprev[i] - 1.0) / sigma[i]
            out_feat[r, 2] = (px / cprev[i] - 1.0) / sigma[i]
            out_feat[r, 3] = start - a
            out_feat[r, 4] = v[a] / advsh[i] if advsh[i] > 0 else np.nan
            out_feat[r, 5] = v[trig] / avgmin if avgmin > 0 else np.nan
            mfe = cu[i] / px - 1.0
            mae = mfe
            # exits
            for x in range(n_x):
                out_ret[r, x] = np.nan
            done = 0
            for j in range(start, nb):
                hi = h[j] / px - 1.0
                lo = l[j] / px - 1.0
                if hi > mfe:
                    mfe = hi
                if lo < mae:
                    mae = lo
                if done == n_x:
                    continue
                for x in range(n_x):
                    if not np.isnan(out_ret[r, x]):
                        continue
                    xt = xtype[x]
                    if xt == 0:
                        continue
                    if xt == 2 or xt == 4:
                        stp = px * (1.0 - xb[x])
                        if l[j] <= stp:
                            out_ret[r, x] = min(stp, o[j]) * 0.995 / px - 1.0
                            done += 1
                            continue
                    if xt == 1 or xt == 4:
                        tg = px * (1.0 + xa[x])
                        if h[j] >= tg * 1.005:
                            out_ret[r, x] = xa[x]
                            done += 1
                            continue
                    if xt == 3 and minute[j] >= emin + xa[x] and j > start:
                        out_ret[r, x] = o[j] / px - 1.0
                        done += 1
            moc = cu[i] / px - 1.0
            for x in range(n_x):
                if np.isnan(out_ret[r, x]):
                    out_ret[r, x] = moc
            out_feat[r, 6] = mfe
            out_feat[r, 7] = mae
            out_oid[r] = i
            out_e[r] = e
            out_min[r] = emin
            out_px[r] = px
            out_stress[r] = stress
            out_sh[r] = sh
            out_qa[r] = qa
            r += 1
    return r


def run(off, bars, orders, entries=ENTRIES, exits=EXITS, position=POSITION):
    """bars: dict of arrays sorted by (order, minute); orders: dict of per-order arrays."""
    n = len(orders["cprev"]) * len(entries)
    et = np.array([ETYPE[e[0]] for e in entries], dtype=np.int64)
    ek = np.array([e[1] for e in entries], dtype=np.float64)
    ep = np.array([e[2] for e in entries], dtype=np.float64)
    eabs = np.array([e[3] if len(e) > 3 else 0.0 for e in entries], dtype=np.float64)
    xt = np.array([XTYPE[x[0]] for x in exits], dtype=np.int64)
    xa = np.array([x[1] for x in exits], dtype=np.float64)
    xb = np.array([x[2] for x in exits], dtype=np.float64)
    out = {
        "oid": np.zeros(n, np.int64),
        "e": np.zeros(n, np.int64),
        "minute": np.zeros(n, np.int64),
        "px": np.zeros(n),
        "stress": np.zeros(n),
        "sh": np.zeros(n),
        "qa": np.zeros(n),
        "feat": np.zeros((n, N_FEAT)),
        "ret": np.zeros((n, len(exits))),
    }
    r = kernel(
        float(position),
        off,
        bars["minute"],
        bars["o"],
        bars["h"],
        bars["l"],
        bars["c"],
        bars["v"],
        orders["cprev"],
        orders["sigma"],
        orders["cu"],
        orders["cutoff"],
        orders["close"],
        orders["advsh"],
        et,
        ek,
        ep,
        eabs,
        xt,
        xa,
        xb,
        out["oid"],
        out["e"],
        out["minute"],
        out["px"],
        out["stress"],
        out["sh"],
        out["qa"],
        out["feat"],
        out["ret"],
    )
    return {k: v[:r] for k, v in out.items()}
