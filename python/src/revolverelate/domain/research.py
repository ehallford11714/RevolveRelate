"""Kineteq-style planner → researcher → reporter → validator after automine.

Citations bind to pipeline evidence only. Local SLM or cloud API is optional.
The SLM never writes SQL and never invents source ids.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from revolverelate.domain.citations import (
    cited_ids,
    collect_citations,
    format_reference,
    forbidden_hits,
    load_research_spec,
    strip_unknown_cites,
)
from revolverelate.slm.probe import probe_slm, slm_wanted


def handoff_from_automine(state: dict | None, spec: dict | None = None) -> dict:
    state = state if isinstance(state, dict) else {}
    spec = spec if isinstance(spec, dict) else load_research_spec()
    honesty = str(state.get("honesty") or spec.get("honesty") or "")
    return {
        "kind": "research_handoff",
        "question": str(state.get("question") or ""),
        "finalQuestion": str(state.get("finalQuestion") or state.get("question") or ""),
        "candidates": list(state.get("candidates") or []),
        "etiologies": list(state.get("etiologies") or []),
        "mined": list(state.get("mined") or []),
        "passes": state.get("passes"),
        "stop": state.get("stop"),
        "domain": str(state.get("domain") or "gene"),
        "evidenceLabel": str(state.get("evidenceLabel") or "possible etiology"),
        "candidateLabel": str(state.get("candidateLabel") or "gene"),
        "gate": state.get("gate") if isinstance(state.get("gate"), dict) else None,
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "honesty": honesty,
    }


def _llm_json(prompt: str, system: str) -> dict | None:
    if not slm_wanted():
        return None
    slm = probe_slm()
    if not slm.available:
        return None
    from revolverelate.slm.complete import complete, extract_json

    try:
        text = complete(prompt, system=system, handle=slm, timeout=180.0)
        data = extract_json(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _plural(label: str) -> str:
    label = str(label or "").strip()
    if not label:
        return ""
    if label.endswith("y") and not label.endswith("ey"):
        return label[:-1] + "ies"
    if label.endswith("s"):
        return label
    return label + "s"


def plan_sections(handoff: dict, spec: dict) -> dict:
    """Planner: spec sections, optional SLM focus that cannot add candidates."""
    base = []
    for row in spec.get("sections") or []:
        if not isinstance(row, dict):
            continue
        base.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or row.get("id") or "Section"),
                "depth": int(row.get("depth") or 2),
                "priority": int(row.get("priority") or 2),
                "focus": "",
            }
        )
    allowed = {c.casefold() for c in (handoff.get("candidates") or [])}
    planned = _llm_json(
        (
            "Create a research-section plan from this automine handoff. "
            f"Do not add {handoff.get('candidateLabel') or 'candidate'} symbols or claims that are not in candidates. "
            "Identification is none. Not proof.\n"
            f"{json.dumps({k: handoff[k] for k in ('question', 'candidates', 'mined', 'honesty') if k in handoff})}\n"
            f"Sections: {json.dumps([{'id': s['id'], 'title': s['title']} for s in base])}\n"
            'Return JSON: {"report_title": str, "research_questions": [str], "sections": [{"id": str, "focus": str}]}'
        ),
        "You are the planner agent. Return JSON only. Never write SQL. Never invent sources.",
    )
    label = str(handoff.get("evidenceLabel") or "possible etiology")
    cand_label = str(handoff.get("candidateLabel") or "gene")
    title = f"{_plural(label).capitalize()}: {handoff.get('question') or 'pipeline findings'}"
    questions = [
        f"Which catalogued {_plural(cand_label)} appear as {_plural(label)} for {handoff.get('question') or 'this question'}?",
        "What live RelOp spans support each candidate?",
        "What remains unidentified?",
    ]
    if planned:
        title = str(planned.get("report_title") or title)
        if isinstance(planned.get("research_questions"), list) and planned["research_questions"]:
            questions = [str(q) for q in planned["research_questions"] if q][:8]
        by_id = {
            str(s.get("id")): s
            for s in (planned.get("sections") or [])
            if isinstance(s, dict)
        }
        for section in base:
            extra = by_id.get(section["id"]) or {}
            focus = str(extra.get("focus") or "")
            if allowed and any(tok and tok.casefold() not in allowed for tok in focus.split() if tok.isupper() and len(tok) > 2):
                focus = ""
            section["focus"] = focus
    return {
        "report_title": title,
        "research_questions": questions,
        "sections": base,
        "backend": "slm" if planned else "spec",
    }


def _cards_blob(citations: list[dict], limit: int = 24) -> str:
    lines = []
    for card in citations[:limit]:
        lines.append(
            f"[{card.get('id')}] kind={card.get('kind')} candidate={card.get('candidate')} "
            f"locator={card.get('locator')} span={card.get('span')}"
        )
    return "\n".join(lines)


def draft_deterministic(handoff: dict, citations: list[dict], plan: dict, spec: dict) -> dict:
    """Reporter fallback: evidence-bound markdown, no LLM required."""
    honesty = str(handoff.get("honesty") or spec.get("honesty") or "")
    question = str(handoff.get("question") or "")
    cands = [str(c) for c in (handoff.get("candidates") or [])]
    etiologies = [e for e in (handoff.get("etiologies") or []) if isinstance(e, dict)]
    by_cand: dict[str, list[str]] = {}
    for card in citations:
        name = str(card.get("candidate") or "")
        if name:
            by_cand.setdefault(name, []).append(str(card["id"]))

    def cites_for(name: str) -> str:
        ids = by_cand.get(name) or []
        return " ".join(f"[{i}]" for i in ids) if ids else ""

    label = str(handoff.get("evidenceLabel") or "possible etiology")
    cand_label = str(handoff.get("candidateLabel") or "gene")
    gate = handoff.get("gate") if isinstance(handoff.get("gate"), dict) else {}
    abstract = (
        f"This report drafts {label} evidence for **{question or 'the pipeline question'}**. "
        f"The automine RelOp loop bound {len(etiologies)} heuristic pair(s) and {len(cands)} candidate(s). "
        + (f"Gate verdict: **{gate.get('overall')}** (per pass: {', '.join(gate.get('perPass') or [])}). " if gate else "")
        + f"Identification is **none**. This is not conclusive proof and not a discovery claim. {honesty}"
    )
    scope = (
        f"Question: {question or '(none)'}. "
        f"Spliced follow-on ask: {handoff.get('finalQuestion') or question or '(none)'}. "
        f"Stop: {handoff.get('stop') or 'n/a'}. Passes: {handoff.get('passes') or 0}. "
        f"Mined catalog follow-ons: {', '.join(handoff.get('mined') or []) or 'none'}."
    )
    if plan.get("research_questions"):
        scope += "\n\nResearch questions:\n" + "\n".join(f"- {q}" for q in plan["research_questions"])
    methods = (
        "The workflow mirrors a planner / researcher / reporter / validator loop. "
        "A dummy RelOp ticket is required before live overlay. "
        "The researcher collects citation cards only from live cause/effect pairs, "
        "`spec/domain-*.json` accessions, and bound KPI rows. "
        "A local SLM (Ollama / OpenAI-compatible) or cloud API may draft prose when present; "
        "otherwise this deterministic draft is used. The SLM never writes SQL and cannot add source ids."
    )
    etio_lines = ["| Candidate | Cue | Source | Citations |", "|---|---|---|---|"]
    if etiologies:
        for row in etiologies:
            src = row.get("source") if isinstance(row.get("source"), dict) else {}
            locator = f"{src.get('entity') or ''}.{src.get('column') or ''}#{src.get('pk') or ''}".strip(".#")
            etio_lines.append(
                f"| {row.get('candidate') or ''} | {row.get('cue') or ''} | {locator} | {cites_for(str(row.get('candidate') or ''))} |"
            )
    else:
        etio_lines.append("| (none bound) |  |  |  |")
    evidence_bits = []
    for card in citations:
        if card.get("kind") != "relop_pair":
            continue
        span = str(card.get("span") or "").strip()
        evidence_bits.append(
            f"- **{card.get('candidate')}** [{card['id']}]. Locator `{card.get('locator')}`. "
            f"{span or 'Catalog mention without a live span.'}"
        )
    if not evidence_bits:
        evidence_bits.append("- No live RelOp pairs were bound. Catalog accessions may still appear as context citations.")
    kpi_bits = [c for c in citations if c.get("kind") == "kpi_row"]
    if kpi_bits:
        kpi_md = "Bound KPI rows from the same RelOp recipe (not an identification):\n\n"
        kpi_md += "\n".join(f"- {c.get('title')} [{c['id']}] - {c.get('span')}" for c in kpi_bits)
    else:
        kpi_md = "No bound KPI rows were attached to this handoff."
    limits = (
        "- Identification is **none**. Evidence grade is **heuristic**.\n"
        f"- Overlay discourse plus catalog symbols are {_plural(label)}, not proof of mechanism.\n"
        "- Dummy sandbox never copies live critical/PII values. Live overlay is TEMP from live non-PII text.\n"
        f"- Follow-on {_plural(cand_label)} are catalogued in spec, not discovered by the engine.\n"
        "- A `supported` gate means live pairs named a catalogued candidate; `review_required` and `refused` passes add no evidence.\n"
        "- SLM prose is discarded if it cites an unknown `[E#]`."
    )
    conclusions = (
        f"The pipeline produced {len(cands)} {label} candidate(s)"
        + (f" ({', '.join(cands)})" if cands else "")
        + f" with {len(citations)} citation card(s). "
        "Treat the list as exploratory evidence for the next RelOp or lab review. "
        "Do not treat a `goalReached` stop as scientific identification."
    )
    refs = "\n\n".join(format_reference(c) for c in citations) or "(no citations)"
    bodies = {
        "abstract": abstract,
        "question_and_scope": scope,
        "methodology": methods,
        "possible_etiologies": "\n".join(etio_lines),
        "evidence": "\n".join(evidence_bits),
        "kpi_context": kpi_md,
        "limitations": limits,
        "conclusions": conclusions,
        "references": refs,
    }
    sections = []
    parts = [f"# {plan.get('report_title') or 'Research report'}", "", f"> **Honesty:** {honesty}", ""]
    for section in plan.get("sections") or []:
        sid = str(section.get("id") or "")
        title = str(section.get("title") or sid)
        body = bodies.get(sid) or section.get("focus") or ""
        sections.append({"id": sid, "title": title, "content": body})
        parts.extend([f"## {title}", "", body, ""])
    markdown = "\n".join(parts).rstrip() + "\n"
    return {"sections": sections, "markdown": markdown, "backend": "deterministic"}


def draft_llm(handoff: dict, citations: list[dict], plan: dict, spec: dict) -> dict | None:
    """Reporter via local or cloud LLM. Must cite only provided cards."""
    honesty = str(handoff.get("honesty") or spec.get("honesty") or "")
    allowed = [c["id"] for c in citations]
    planned = _llm_json(
        (
            "Draft a citation-grounded research report from pipeline evidence only.\n"
            f"HONESTY: {honesty}\n"
            "Rules: identification is none; not conclusive proof; not medical advice; never invent [E#]; "
            f"use only these citation ids: {', '.join(allowed) or '(none)'}.\n"
            f"HANDOFF: {json.dumps({k: handoff[k] for k in ('question', 'candidates', 'mined', 'stop', 'passes')})}\n"
            f"CITATIONS:\n{_cards_blob(citations)}\n"
            f"SECTIONS: {json.dumps([{'id': s['id'], 'title': s['title']} for s in plan.get('sections') or []])}\n"
            'Return JSON: {"report_title": str, "sections": [{"id": str, "title": str, "content": str}]}'
        ),
        "You are the reporter agent. Return JSON only. Never write SQL. Never invent citations.",
    )
    if not planned or not isinstance(planned.get("sections"), list):
        return None
    by_id = {str(s.get("id")): s for s in planned["sections"] if isinstance(s, dict)}
    sections = []
    title = str(planned.get("report_title") or plan.get("report_title") or "Research report")
    parts = [f"# {title}", "", f"> **Honesty:** {honesty}", ""]
    fallback = draft_deterministic(handoff, citations, plan, spec)
    fall_map = {s["id"]: s["content"] for s in fallback["sections"]}
    for section in plan.get("sections") or []:
        sid = str(section.get("id") or "")
        heading = str(section.get("title") or sid)
        extra = by_id.get(sid) or {}
        body = str(extra.get("content") or "").strip() or str(fall_map.get(sid) or "")
        if sid == "references":
            body = "\n\n".join(format_reference(c) for c in citations) or body
        sections.append({"id": sid, "title": heading, "content": body})
        parts.extend([f"## {heading}", "", body, ""])
    return {"sections": sections, "markdown": "\n".join(parts).rstrip() + "\n", "backend": "slm"}


def validate_draft(markdown: str, citations: list[dict], spec: dict) -> dict:
    """Validator: unknown [E#] are stripped; honesty banner is required."""
    allowed = {str(c.get("id")) for c in citations if c.get("id")}
    cleaned, unknown = strip_unknown_cites(markdown, allowed)
    honesty = str(spec.get("honesty") or "")
    if honesty and "**Honesty:**" not in cleaned:
        cleaned = f"> **Honesty:** {honesty}\n\n{cleaned}"
    used = cited_ids(cleaned)
    scan = cleaned
    if "**Honesty:**" in cleaned:
        parts = cleaned.split("\n\n", 1)
        scan = parts[1] if len(parts) > 1 else ""
    forbid = forbidden_hits(scan)
    if forbid:
        cleaned = (
            cleaned.rstrip()
            + "\n\n> Validator note: phrasing that sounded like proof or discovery was flagged "
            f"({', '.join(forbid)}). Identification remains none.\n"
        )
    passed = len(unknown) == 0 and (not citations or len(used) > 0)
    return {
        "passed": passed,
        "unknownCitations": unknown,
        "cited": used,
        "forbidden": forbid,
        "citationCount": len(citations),
        "identification": "none",
        "conclusive": False,
        "markdown": cleaned,
    }


def compile_report(
    handoff: dict,
    plan: dict,
    citations: list[dict],
    draft: dict,
    checked: dict,
    agents: list[dict],
    spec: dict,
    *,
    slm: dict | None = None,
) -> dict:
    return {
        "kind": "research_report",
        "title": plan.get("report_title") or f"{_plural(str(handoff.get('evidenceLabel') or 'possible etiology')).capitalize()}: {handoff.get('question') or ''}",
        "style": spec.get("style") or "science",
        "citationFormat": spec.get("citationFormat") or "numeric",
        "question": handoff.get("question"),
        "markdown": checked.get("markdown") or draft.get("markdown") or "",
        "sections": draft.get("sections") or [],
        "citations": citations,
        "handoff": handoff,
        "plan": {k: plan[k] for k in plan if k != "sections"},
        "agents": agents,
        "validation": {k: checked[k] for k in checked if k != "markdown"},
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "honesty": handoff.get("honesty") or spec.get("honesty"),
        "llm": slm or {"kind": "none", "available": False},
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _save_report(workdir: Path | None, report: dict) -> dict:
    if workdir is None:
        return report
    dest = Path(workdir) / ".revolverelate"
    dest.mkdir(parents=True, exist_ok=True)
    json_path = dest / "report.json"
    md_path = dest / "report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(str(report.get("markdown") or ""), encoding="utf-8")
    report["paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def run_research(state: dict, *, workdir: Path | str | None = None, use_slm: bool = True) -> dict:
    """Run planner → researcher → reporter → validator on an automine state."""
    spec = load_research_spec()
    handoff = handoff_from_automine(state, spec)
    agents: list[dict] = []
    plan = plan_sections(handoff, spec)
    agents.append({"agent": "planner", "backend": plan.get("backend"), "sections": len(plan.get("sections") or [])})
    citations = collect_citations(state)
    agents.append(
        {
            "agent": "researcher",
            "backend": "pipeline",
            "citations": len(citations),
            "kinds": sorted({str(c.get("kind")) for c in citations}),
        }
    )
    slm_info = None
    draft = None
    if use_slm and slm_wanted():
        handle = probe_slm()
        slm_info = handle.to_dict()
        if handle.available:
            draft = draft_llm(handoff, citations, plan, spec)
    if draft is None:
        draft = draft_deterministic(handoff, citations, plan, spec)
    agents.append({"agent": "reporter", "backend": draft.get("backend")})
    checked = validate_draft(str(draft.get("markdown") or ""), citations, spec)
    draft["markdown"] = checked["markdown"]
    agents.append(
        {
            "agent": "validator",
            "backend": "rules",
            "passed": checked["passed"],
            "unknownCitations": checked["unknownCitations"],
            "cited": checked["cited"],
        }
    )
    report = compile_report(handoff, plan, citations, draft, checked, agents, spec, slm=slm_info)
    return _save_report(Path(workdir) if workdir else None, report)
