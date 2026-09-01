"""Self-directed hypothesis loop — spec/hypotheses.json.

No objective is needed. The engine surveys the booted schema, forms testable
hypotheses about bound columns, tests each one as a RelOp chain (dummy ticket
first, verdict from live rows), derives new hypotheses from what it learned,
and remembers every test so nothing is re-run.

A hypothesis is a descriptive statement with a declared threshold. Verdicts are
threshold comparisons on live rows. Identification is none. Never SQL.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from revolverelate.analytics.asklog import record_ask
from revolverelate.analytics.bind import is_measure, list_dimensions, pick_date, pick_fact, resolve_column
from revolverelate.analytics.composites import check_chain
from revolverelate.catalog import spec_dir
from revolverelate.vector.embed import fingerprint

_VIRTUAL_ENTITIES = {"overlaychunk", "asklog", "automineevidence"}
_SKIP_DIM_SUFFIX = ("code", "id", "name", "date", "at", "key", "url", "hash")
_MAX_SAMPLE_LEN = 24
_ORIGIN_WEIGHT = {"derive": 0, "automine": 1, "domain": 2, "slm": 3, "template": 4}


@lru_cache(maxsize=1)
def load_hypotheses_spec() -> dict:
    return json.loads((spec_dir() / "hypotheses.json").read_text(encoding="utf-8"))


def _kind_spec(kind: str) -> dict:
    for row in load_hypotheses_spec()["kinds"]:
        if row["id"] == kind:
            return row
    raise KeyError(kind)


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# ---------------------------------------------------------------- hypothesis


@dataclass
class Hypothesis:
    kind: str
    binds: dict
    slice: dict | None = None
    origin: str = "template"
    parent: int | None = None
    threshold: dict = field(default_factory=dict)
    priority: tuple = field(default_factory=tuple)

    @property
    def key(self) -> int:
        return fingerprint(json.dumps({"kind": self.kind, "binds": self.binds, "slice": self.slice}, sort_keys=True, default=str))

    def statement(self) -> str:
        ctx = _Safe(**self.binds, **self.threshold)
        text = _kind_spec(self.kind)["statement"].format_map(ctx)
        if self.slice:
            for lead in ("The ", "Total ", "Average "):
                if text.startswith(lead):
                    text = lead.lower() + text[len(lead) :]
                    break
            text = f"Within {self.slice['column']} = {self.slice['value']}, " + text
        return text

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "statement": self.statement(),
            "binds": dict(self.binds),
            "slice": dict(self.slice) if self.slice else None,
            "threshold": dict(self.threshold),
            "origin": self.origin,
            "parent": self.parent,
        }


def _new(kind: str, binds: dict, *, slice_: dict | None = None, origin: str = "template", parent: int | None = None, priority: tuple = ()) -> Hypothesis:
    return Hypothesis(kind=kind, binds=binds, slice=slice_, origin=origin, parent=parent, threshold=dict(_kind_spec(kind).get("threshold") or {}), priority=priority)


# ---------------------------------------------------------------- survey


def _hops(graph, fact) -> dict[str, int]:
    """Undirected hop count from the fact entity, so near dimensions are tried first."""
    adj: dict[str, set[str]] = {}
    for rel in graph.relationships:
        a, b = rel.from_entity.casefold(), rel.to_entity.casefold()
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    dist = {fact.name.casefold(): 0}
    todo = deque([fact.name.casefold()])
    while todo:
        cur = todo.popleft()
        for nxt in adj.get(cur, ()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                todo.append(nxt)
    return dist


def _usable_dimension(graph, name: str) -> tuple[bool, list]:
    try:
        bound = resolve_column(graph, name)
    except Exception:
        return False, []
    if bound.entity.name.casefold() in _VIRTUAL_ENTITIES:
        return False, []
    low = name.replace("_", "").casefold()
    if low.endswith(_SKIP_DIM_SUFFIX):
        return False, []
    samples = [s for s in (bound.attr.samples or ()) if s is not None and str(s).strip()]
    if not samples:
        return False, []
    if sum(len(str(s)) for s in samples) / len(samples) > _MAX_SAMPLE_LEN:
        return False, []
    return True, samples


def _years(bound) -> list[int]:
    if bound is None:
        return []
    years = set()
    for s in bound.attr.samples or ():
        m = re.match(r"(\d{4})", str(s))
        if m:
            years.add(int(m.group(1)))
    return sorted(years)


def survey(graph, spec: dict, domain=None) -> dict:
    """Bound facts about the schema the loop may form hypotheses over."""
    budget = spec["budget"]
    fact = pick_fact(graph)
    measures = [a.name for a in fact.attributes if is_measure(a)]
    binds = (domain.spec.get("binds") if domain is not None else None) or {}
    for pref in (binds.get("treatment"), binds.get("measure")):
        if pref and pref in measures:
            measures.remove(pref)
            measures.insert(0, pref)
    hops = _hops(graph, fact)
    dims: list[tuple[int, str, list]] = []
    for name in list_dimensions(graph):
        ok, samples = _usable_dimension(graph, name)
        if not ok:
            continue
        ent = resolve_column(graph, name).entity.name.casefold()
        rank = hops.get(ent, 9)
        if binds.get("dimension") and str(binds["dimension"]).casefold() == name.casefold():
            rank = -1
        dims.append((rank, name, samples))
    dims.sort(key=lambda t: (t[0], t[1]))
    date = pick_date(graph)
    return {
        "fact": fact.name,
        "measures": measures[: int(budget["maxMeasures"])],
        "allMeasures": measures,
        "dimensions": [d[1] for d in dims[: int(budget["maxDimensions"])]],
        "values": {d[1]: [str(v) for v in d[2][: int(budget["maxValuesPerDimension"])]] for d in dims},
        "date": date.attr.name if date is not None else None,
        "years": _years(date),
        "domain": domain.id if domain is not None else None,
        "domainBinds": dict(binds),
    }


# ---------------------------------------------------------------- form


def form_from_templates(view: dict) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    measures, dims, values = view["measures"], view["dimensions"], view["values"]
    for mi, m in enumerate(measures):
        for di, d in enumerate(dims):
            out.append(_new("concentration", {"measure": m, "dimension": d}, priority=(mi, di, 0)))
            for vi, v in enumerate(values.get(d) or []):
                out.append(_new("contrast", {"measure": m, "dimension": d, "value": v}, priority=(mi, di, vi + 1)))
    if measures:
        m0 = measures[0]
        for ti, t in enumerate(view["allMeasures"]):
            if t == m0:
                continue
            out.append(_new("association", {"measure": m0, "treatment": t}, priority=(0, ti, 0)))
            out.append(_new("correlation", {"measure": m0, "measure2": t}, priority=(0, ti, 1)))
    years = view["years"]
    if len(years) >= 2:
        y1, y2 = years[-2], years[-1]
        for mi, m in enumerate(measures):
            if view["date"]:
                out.append(_new("trend", {"measure": m, "date": view["date"], "year1": y1, "year2": y2}, priority=(mi, 0, 0)))
    return out


def form_from_automine(rr, view: dict, domain) -> list[Hypothesis]:
    """Catalogued candidates the autominer already paired become contrast hypotheses."""
    if domain is None or not view["measures"]:
        return []
    path = Path(rr.workdir) / ".revolverelate" / "automine.json"
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    col = str(domain.automine.get("symbolColumn") or "")
    if not col or col.casefold() not in {d.casefold() for d in view["dimensions"]}:
        return []
    dim = next(d for d in view["dimensions"] if d.casefold() == col.casefold())
    out = []
    for i, cand in enumerate(list(state.get("candidates") or [])[:5]):
        out.append(_new("contrast", {"measure": view["measures"][0], "dimension": dim, "value": str(cand)}, origin="automine", priority=(0, i, 0)))
    return out


def form_from_slm(view: dict) -> list[Hypothesis]:
    """Optional: an SLM may propose extra hypotheses, but only as bound names we can check. Never SQL."""
    from revolverelate.slm.probe import probe_slm, slm_wanted

    if not slm_wanted():
        return []
    try:
        handle = probe_slm()
        if not handle.available:
            return []
        from revolverelate.slm.complete import complete, extract_json

        prompt = json.dumps(
            {
                "measures": view["allMeasures"],
                "dimensions": view["dimensions"],
                "sampleValues": view["values"],
                "kinds": [k["id"] for k in load_hypotheses_spec()["kinds"]],
                "reply": {"hypotheses": [{"kind": "contrast", "measure": "<measure>", "dimension": "<dimension>", "value": "<sample value>"}]},
            }
        )
        text = complete(prompt, system="Propose at most 5 testable hypotheses as JSON using only the listed names. No SQL. No prose.", handle=handle, timeout=60.0)
        data = extract_json(text)
    except Exception:
        return []
    rows = data.get("hypotheses") if isinstance(data, dict) else None
    out = []
    kinds = {k["id"] for k in load_hypotheses_spec()["kinds"]}
    all_m = {m.casefold(): m for m in view["allMeasures"]}
    all_d = {d.casefold(): d for d in view["dimensions"]}
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict) or row.get("kind") not in kinds:
            continue
        m = all_m.get(str(row.get("measure") or "").casefold())
        if not m:
            continue
        binds: dict = {"measure": m}
        kind = str(row["kind"])
        if kind in {"concentration", "contrast"}:
            d = all_d.get(str(row.get("dimension") or "").casefold())
            if not d:
                continue
            binds["dimension"] = d
            if kind == "contrast":
                v = str(row.get("value") or "")
                if v not in (view["values"].get(d) or []):
                    continue
                binds["value"] = v
        elif kind == "association":
            t = all_m.get(str(row.get("treatment") or "").casefold())
            if not t or t == m:
                continue
            binds["treatment"] = t
        elif kind == "correlation":
            t = all_m.get(str(row.get("measure2") or "").casefold())
            if not t or t == m:
                continue
            binds["measure2"] = t
        else:
            continue
        out.append(_new(kind, binds, origin="slm", priority=(0, i, 0)))
        if len(out) >= 5:
            break
    return out


def prioritise(pool: list[Hypothesis], tested: set[int]) -> list[Hypothesis]:
    """Drop what has been tested, then interleave kinds so one round covers several question shapes."""
    fresh: dict[int, Hypothesis] = {}
    for h in pool:
        if h.key not in tested and h.key not in fresh:
            fresh[h.key] = h
    by_kind: dict[str, list[Hypothesis]] = {}
    for h in fresh.values():
        by_kind.setdefault(h.kind, []).append(h)
    for rows in by_kind.values():
        rows.sort(key=lambda h: (_ORIGIN_WEIGHT.get(h.origin.split(":")[0], 9), h.priority))
    order = [k["id"] for k in load_hypotheses_spec()["kinds"]]
    queues = [by_kind[k] for k in order if k in by_kind]
    out: list[Hypothesis] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    out.sort(key=lambda h: _ORIGIN_WEIGHT.get(h.origin.split(":")[0], 9) == 0, reverse=True)
    return out


# ---------------------------------------------------------------- test


def _slice_steps(h: Hypothesis) -> list[dict]:
    return [{"op": "eq", "column": h.slice["column"], "value": h.slice["value"]}] if h.slice else []


def _run(rr, steps: list[dict], *, live: bool, prefix: str, key: int, tag: str) -> dict:
    """Grammar check → dummy rollout (the ticket) → live replay. Rows for the verdict come from live when enabled."""
    report = check_chain(steps)
    out = {"ops": [str(s.get("op")) for s in steps], "legal": bool(report["ok"]), "issues": report["issues"], "ran": False, "planId": None, "rows": [], "grade": None, "error": None}
    if not report["ok"]:
        return out
    plan_id = re.sub(r"[^a-z0-9]+", "-", f"{prefix}-{key}-{tag}".casefold()).strip("-")
    try:
        plan = rr.analytics.run_chain(steps, plan_id=plan_id)
    except Exception as exc:
        out["error"] = str(exc)[:200]
        return out
    out.update({"ran": plan.get("status") == "sandbox_ok", "planId": plan.get("id"), "dummyRows": int(plan.get("rowCount") or 0), "columns": plan.get("columns")})
    if not out["ran"]:
        return out
    if live:
        try:
            replay = rr.replay_live(plan_id=plan["id"])
            out["rows"] = list(replay.get("rows") or [])
            out["grade"] = "live"
        except Exception as exc:
            out["error"] = str(exc)[:200]
            out["ran"] = False
    else:
        out["rows"] = list(plan.get("rows") or [])
        out["grade"] = "dummy"
    return out


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _pearson(pairs: list) -> tuple[float | None, int]:
    xs, ys = [], []
    for row in pairs:
        if len(row) < 2:
            continue
        a, b = _num(row[0]), _num(row[1])
        if a is None or b is None:
            continue
        xs.append(a)
        ys.append(b)
    n = len(xs)
    if n < 2:
        return None, n
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None, n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy), n


def evaluate_hypothesis(rr, h: Hypothesis, *, live: bool, prefix: str = "hyp") -> dict:
    """Run the chain(s) for one hypothesis and decide supported / refuted / inconclusive / illegal / failed."""
    m = h.binds.get("measure")
    th = h.threshold
    runs: list[dict] = []
    effect: dict = {}
    verdict = "inconclusive"
    why = ""

    def go(steps, tag):
        r = _run(rr, steps, live=live, prefix=prefix, key=h.key, tag=tag)
        runs.append(r)
        return r

    if h.kind == "concentration":
        d = h.binds["dimension"]
        r = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "win_share_total", "measure": m, "dimension": d}, {"op": "sort_value_desc"}], "share")
        if r["ran"] and len(r["rows"]) < 2:
            why = "fewer than two groups — concentration is trivial"
        elif r["ran"] and len(r["rows"][0]) >= 3:
            top, val, total = r["rows"][0][0], _num(r["rows"][0][1]), _num(r["rows"][0][2])
            if total and val is not None:
                share = val / total
                effect = {"top": top, "share": round(share, 4), "threshold": th["p"], "groups": len(r["rows"])}
                verdict = "supported" if share >= float(th["p"]) else "refuted"
                why = f"top {d} is {top} with {share:.1%} of {m} over {len(r['rows'])} groups"
            else:
                why = "null total"
    elif h.kind == "contrast":
        d, v = h.binds["dimension"], str(h.binds["value"])
        r = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "agg_sum_by", "measure": m, "dimension": d}, {"op": "vs_peer", "measure": m, "dimension": d}], "peer")
        if r["ran"]:
            hit = next((row for row in r["rows"] if len(row) >= 3 and str(row[0]).casefold() == v.casefold()), None)
            if hit is None:
                why = f"{d} = {v} absent in rows"
            else:
                val, peer = _num(hit[1]), _num(hit[2])
                if val is None or not peer or peer <= 0:
                    why = "null or non-positive peer mean"
                else:
                    ratio = val / peer
                    effect = {"value": round(val, 4), "peer": round(peer, 4), "ratio": round(ratio, 4), "threshold": th["ratio"], "groups": len(r["rows"])}
                    verdict = "supported" if ratio >= float(th["ratio"]) else "refuted"
                    why = f"{m} for {d} = {v} is {ratio:.2f}x the peer mean over {len(r['rows'])} groups"
    elif h.kind == "association":
        t = h.binds["treatment"]
        r1 = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "median", "measure": t}], "median")
        med = _num(r1["rows"][0][0]) if r1["ran"] and r1["rows"] else None
        n = int(_num(r1["rows"][0][3]) or 0) if r1["ran"] and r1["rows"] and len(r1["rows"][0]) >= 4 else 0
        if med is None:
            why = "no median"
        elif n < 10:
            why = f"only {n} rows"
        else:
            r2 = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "agg_avg", "measure": m}], "avg-all")
            r3 = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "measure_above", "measure": t, "threshold": med}, {"op": "agg_avg", "measure": m}], "avg-above")
            base = _num(r2["rows"][0][0]) if r2["ran"] and r2["rows"] else None
            high = _num(r3["rows"][0][0]) if r3["ran"] and r3["rows"] else None
            if base is None or high is None:
                why = "null average"
            elif base <= 0:
                why = "non-positive baseline average"
            else:
                ratio = high / base
                effect = {"median": round(med, 6), "avgAll": round(base, 6), "avgAboveMedian": round(high, 6), "ratio": round(ratio, 4), "n": n, "threshold": th["ratio"]}
                verdict = "supported" if ratio >= float(th["ratio"]) else "refuted"
                why = f"avg {m} is {ratio:.2f}x higher where {t} > median ({med:g}), n={n}"
    elif h.kind == "correlation":
        t = h.binds["measure2"]
        r = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "corr", "measure": m, "measure2": t}], "corr")
        if r["ran"]:
            rho, n = _pearson(r["rows"])
            if n < int(th["minPairs"]):
                why = f"only {n} pairs (< {th['minPairs']})"
            elif rho is None:
                why = "zero variance"
            else:
                effect = {"r": round(rho, 4), "n": n, "threshold": th["r"]}
                verdict = "supported" if abs(rho) >= float(th["r"]) else "refuted"
                why = f"Pearson r = {rho:.3f} over {n} pairs"
    elif h.kind == "trend":
        date, y1, y2 = h.binds["date"], int(h.binds["year1"]), int(h.binds["year2"])
        r1 = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "period_year", "date": date, "year": y1}, {"op": "agg_sum", "measure": m}], f"y{y1}")
        r2 = go([{"op": "scan_fact"}, *_slice_steps(h), {"op": "period_year", "date": date, "year": y2}, {"op": "agg_sum", "measure": m}], f"y{y2}")
        s1 = _num(r1["rows"][0][0]) if r1["ran"] and r1["rows"] else None
        s2 = _num(r2["rows"][0][0]) if r2["ran"] and r2["rows"] else None
        if s1 is None or s2 is None or s1 == 0:
            why = "a year has no rows or a zero total"
        else:
            growth = (s2 - s1) / abs(s1)
            effect = {"sumYear1": round(s1, 4), "sumYear2": round(s2, 4), "growth": round(growth, 4), "threshold": th["growth"]}
            verdict = "supported" if growth >= float(th["growth"]) else "refuted"
            why = f"{m} went {s1:g} -> {s2:g} ({growth:+.1%}) from {y1} to {y2}"
    else:
        why = f"unknown kind {h.kind}"

    if any(not r["legal"] for r in runs):
        verdict, why = "illegal", "; ".join(str(i) for r in runs for i in r["issues"])[:200] or "grammar check failed"
    elif any(r["error"] for r in runs):
        verdict, why = "failed", next(r["error"] for r in runs if r["error"])
    grade = "live" if runs and all(r.get("grade") == "live" for r in runs if r["ran"]) and any(r["ran"] for r in runs) else "dummy"
    result_verdict = verdict
    if grade == "dummy" and verdict in {"supported", "refuted", "inconclusive"}:
        result_verdict = "dummy_only"
    row = h.to_dict() | {
        "verdict": result_verdict,
        "dummyVerdict": verdict if result_verdict == "dummy_only" else None,
        "grade": grade,
        "effect": effect,
        "why": why,
        "chains": [{k: r.get(k) for k in ("ops", "legal", "ran", "planId", "dummyRows", "error")} | {"liveRows": len(r["rows"]) if r.get("grade") == "live" else 0} for r in runs],
        "testedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return row


# ---------------------------------------------------------------- derive


def derive(result: dict, view: dict, spec: dict) -> list[Hypothesis]:
    """Form new hypotheses from a supported result. Drill into the winning slice; try to refute by generalising to peers."""
    if result["verdict"] != "supported":
        return []
    rules = list((spec["derive"]["onSupported"] or {}).get(result["kind"]) or [])
    cap = int(spec["budget"]["followUpsPerSupported"])
    binds, sl, key = result["binds"], result.get("slice"), int(result["key"])
    m = binds.get("measure")
    out: list[Hypothesis] = []

    def other_dims(exclude: str | None) -> list[str]:
        return [d for d in view["dimensions"] if d.casefold() != str(exclude or "").casefold() and not (sl and d.casefold() == str(sl["column"]).casefold())]

    for rule in rules:
        if rule == "contrast_top" and result["effect"].get("top") is not None:
            out.append(_new("contrast", {"measure": m, "dimension": binds["dimension"], "value": str(result["effect"]["top"])}, slice_=sl, origin="derive:contrast_top", parent=key))
        elif rule == "drill":
            top = result["effect"].get("top") if result["kind"] == "concentration" else binds.get("value")
            if top is None or sl:
                continue
            new_slice = {"column": binds["dimension"], "value": str(top)}
            for d2 in other_dims(binds["dimension"])[:cap]:
                out.append(_new("concentration", {"measure": m, "dimension": d2}, slice_=new_slice, origin="derive:drill", parent=key))
        elif rule == "refute_peers":
            for v2 in (view["values"].get(binds["dimension"]) or []):
                if v2.casefold() != str(binds["value"]).casefold():
                    out.append(_new("contrast", {"measure": m, "dimension": binds["dimension"], "value": v2}, slice_=sl, origin="derive:refute_peers", parent=key))
        elif rule in {"drill_association", "drill_correlation", "drill_trend"} and not sl:
            for d in other_dims(None)[:1]:
                for v in (view["values"].get(d) or [])[:cap]:
                    out.append(_new(result["kind"], dict(binds), slice_={"column": d, "value": v}, origin=f"derive:{rule}", parent=key))
    return out[: cap * 2]


# ---------------------------------------------------------------- memory


def _memory_path(workdir, spec: dict) -> Path:
    return Path(workdir) / spec["seeds"]["memoryFile"]


def load_tested(workdir, spec: dict) -> dict[int, dict]:
    path = _memory_path(workdir, spec)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {int(r["key"]): r for r in (state.get("tested") or []) if isinstance(r, dict) and "key" in r}


def _remember(rr, supported: list[dict], *, domain_id: str, round_no: int) -> int:
    from revolverelate.domain.evidence_store import remember_evidence

    rows = []
    for r in supported:
        cand = str((r.get("slice") or {}).get("value") or r["binds"].get("value") or r["effect"].get("top") or r["binds"].get("dimension") or r["binds"].get("measure"))
        rows.append({"candidate": cand, "cue": "hypothesis", "cause": r["statement"], "effect": f"{r['verdict']}: {r['why']}", "pass": round_no, "source": {"pk": str(r["key"])}})
    try:
        return remember_evidence(rr.sandbox, rows, domain=domain_id, question="self-directed hypotheses", pass_no=round_no)
    except Exception:
        return 0


def _objective_from(result: dict) -> str:
    b, sl = result["binds"], result.get("slice")
    text = f"{b.get('measure')} by {b.get('dimension') or b.get('treatment') or b.get('measure2') or 'dimension'}"
    if sl:
        text += f" for {sl['column']} {sl['value']}"
    elif b.get("value"):
        text += f" for {b['dimension']} {b['value']}"
    return text


# ---------------------------------------------------------------- runner


def run_hypotheses(
    rr,
    *,
    rounds: int | None = None,
    per_round: int | None = None,
    live: bool = True,
    retest: bool = False,
    search: bool | None = None,
    domain: str | None = None,
    use_slm: bool = True,
) -> dict:
    """Survey → form → prioritise → test → derive → remember, until the budget or the hypothesis pool runs out."""
    from revolverelate.domain.registry import detect_domain

    spec = load_hypotheses_spec()
    budget = spec["budget"]
    n_rounds = min(int(rounds or budget["rounds"]), int(budget["hardMaxRounds"]))
    per = int(per_round or budget["perRound"])
    hard_max = int(budget["hardMaxTests"])
    graph = rr.schema
    dom = detect_domain(graph, prefer=domain)
    view = survey(graph, spec, dom)

    tested_before = {} if retest else load_tested(rr.workdir, spec)
    tested: set[int] = set(tested_before)
    pool: list[Hypothesis] = form_from_templates(view)
    seeds = spec["seeds"]
    if seeds.get("fromAutomine"):
        pool = form_from_automine(rr, view, dom) + pool
    if seeds.get("fromSlm") and use_slm:
        pool = form_from_slm(view) + pool
    formed = len(pool)

    results: list[dict] = []
    derived_total = 0
    stop = "rounds"
    round_no = 0
    remembered = 0
    for round_no in range(1, n_rounds + 1):
        queue = prioritise(pool, tested)
        if not queue:
            stop = "exhausted"
            break
        batch = queue[:per]
        round_results = []
        for h in batch:
            if len(results) >= hard_max:
                stop = "hardMaxTests"
                break
            res = evaluate_hypothesis(rr, h, live=live, prefix=f"hyp-r{round_no}")
            res["round"] = round_no
            tested.add(h.key)
            results.append(res)
            round_results.append(res)
            record = {"question": res["statement"], "objective": "self-directed hypotheses", "status": "sandbox_ok" if res["verdict"] not in {"illegal", "failed"} else "bind_failed"}
            try:
                record_ask(rr.sandbox, question=record["question"], objective=record["objective"], ir=None, status=record["status"], composite=(res["chains"][0]["planId"] if res["chains"] else "") or "", pattern="hypothesis", score=1.0 if res["verdict"] == "supported" else 0.0, row_count=sum(int(c.get("liveRows") or 0) for c in res["chains"]))
            except Exception:
                pass
        if stop == "hardMaxTests":
            break
        fresh: list[Hypothesis] = []
        for res in round_results:
            fresh.extend(derive(res, view, spec))
        derived_total += len(fresh)
        pool = fresh + pool
        supported_now = [r for r in round_results if r["verdict"] == "supported"]
        if seeds.get("evidenceMemory") and supported_now:
            remembered += _remember(rr, supported_now, domain_id=(dom.id if dom else "schema"), round_no=round_no)

    supported = [r for r in results if r["verdict"] == "supported"]
    refuted = [r for r in results if r["verdict"] == "refuted"]
    peer_notes = []
    for r in supported:
        if r["origin"] == "derive:refute_peers":
            parent = next((p for p in results if int(p["key"]) == int(r["parent"] or 0)), None)
            if parent:
                peer_notes.append(f"{parent['statement']} also holds for {r['binds'].get('value')} — less specific than it looked.")

    search_out = None
    do_search = spec["search"].get("afterSupported") if search is None else search
    if do_search and supported:
        from revolverelate.analytics.autonomy import run_autonomy

        lead = next((r for r in supported if r["kind"] in {"contrast", "concentration"}), supported[0])
        try:
            search_out = run_autonomy(rr, _objective_from(lead), generations=int(spec["search"]["generations"]), live=live)
            search_out = {"objective": search_out["objective"], "best": search_out["best"], "stop": search_out["stop"], "winnerOps": (search_out.get("winner") or {}).get("ops"), "live": bool(search_out.get("live"))}
        except Exception as exc:
            search_out = {"error": str(exc)[:200]}

    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in spec["verdicts"]}
    state = {
        "domain": dom.id if dom else None,
        "survey": {k: view[k] for k in ("fact", "measures", "dimensions", "date", "years")},
        "rounds": round_no if results else 0,
        "formed": formed,
        "derived": derived_total,
        "tested": results,
        "previouslyTested": len(tested_before),
        "skippedAsTested": len(tested_before),
        "counts": counts,
        "supported": [{"statement": r["statement"], "effect": r["effect"], "origin": r["origin"]} for r in supported],
        "refuted": [{"statement": r["statement"], "effect": r["effect"]} for r in refuted],
        "peerNotes": peer_notes,
        "remembered": remembered,
        "stop": stop,
        "search": search_out,
        "grade": "live" if live else "dummy_only",
        "identification": "none",
        "honesty": spec["honesty"],
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = _memory_path(rr.workdir, spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = list(tested_before.values()) + results
    path.write_text(json.dumps(state | {"tested": merged}, indent=2, default=str), encoding="utf-8")
    return state
