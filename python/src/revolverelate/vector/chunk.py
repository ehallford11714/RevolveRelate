"""Text → retrieve units. Semantic, causal, topic, discourse, and event splits."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache

from revolverelate.catalog import spec_dir
from revolverelate.vector.embed import cosine, hash_embed, tokenize

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"\s*;\s*|\s+—\s+|\s+--\s+|(?:,\s+and\s+)|(?:\s+but\s+)")


@lru_cache(maxsize=1)
def load_rag_spec() -> dict:
    return json.loads((spec_dir() / "vector-rag.json").read_text(encoding="utf-8"))


def causal_taxonomy() -> dict[str, tuple[str, ...]]:
    raw = load_rag_spec().get("causalTaxonomy") or {}
    return {role: tuple(cues) for role, cues in raw.items()}


def causal_cues() -> tuple[str, ...]:
    spec = load_rag_spec()
    flat = spec.get("causalCues") or []
    if flat:
        return tuple(sorted(set(flat), key=len, reverse=True))
    cues: list[str] = []
    for items in causal_taxonomy().values():
        cues.extend(items)
    return tuple(sorted(set(cues), key=len, reverse=True))


def _cue_role(cue: str) -> str:
    key = cue.casefold()
    for role, items in causal_taxonomy().items():
        if key in {x.casefold() for x in items}:
            return role
    return "residual"


def sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p and p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def _window(tokens: list[str], size: int, overlap: int) -> list[str]:
    size = max(int(size or 8), 1)
    overlap = max(0, min(int(overlap), size - 1))
    step = max(size - overlap, 1)
    if not tokens:
        return []
    out = []
    i = 0
    while i < len(tokens):
        out.append(" ".join(tokens[i : i + size]))
        if i + size >= len(tokens):
            break
        i += step
    return out


def _unit(text: str, level: str, **extra) -> dict:
    row = {"text": text, "level": level, "cue": extra.get("cue") or "", "role": extra.get("role") or "", "score": extra.get("score")}
    if "parent" in extra:
        row["parent"] = extra["parent"]
    return row


def chunk_fixed(text: str, n: int = 12) -> list[dict]:
    return [_unit(piece, "chunk") for piece in _window(tokenize(text), n, 0)]


def chunk_token(text: str, n: int = 12) -> list[dict]:
    return [_unit(piece, "chunk") for piece in _window(tokenize(text), n, max(n // 4, 1))]


def chunk_sentence(text: str) -> list[dict]:
    return [_unit(s, "sentence") for s in sentences(text)]


def chunk_window(text: str, n: int = 2) -> list[dict]:
    sents = sentences(text)
    n = max(int(n or 2), 1)
    if not sents:
        return []
    return [_unit(" ".join(sents[i : i + n]), "window") for i in range(len(sents))]


def chunk_recursive(text: str, n: int = 16) -> list[dict]:
    limit = max(int(n or 16), 4)
    out: list[dict] = []

    def walk(piece: str, seps: list[str]) -> None:
        piece = piece.strip()
        if not piece:
            return
        if len(tokenize(piece)) <= limit or not seps:
            out.append(_unit(piece, "chunk"))
            return
        sep = seps[0]
        parts = [p.strip() for p in re.split(sep, piece) if p.strip()] if sep != " " else _window(tokenize(piece), limit, 0)
        if len(parts) <= 1:
            walk(piece, seps[1:])
            return
        for part in parts:
            walk(part, seps[1:])

    walk(text, [r"\n\n+", r"\n+", r"(?<=[.!?])\s+", " "])
    return out or [_unit((text or "").strip(), "chunk")]


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    p = min(max(float(p), 0.0), 1.0)
    idx = p * (len(ys) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ys) - 1)
    frac = idx - lo
    return ys[lo] * (1.0 - frac) + ys[hi] * frac


def _mean_norm(vecs: list[list[float]]) -> list[float]:
    if not vecs:
        return []
    dim = len(vecs[0])
    acc = [0.0] * dim
    for vec in vecs:
        for i, v in enumerate(vec[:dim]):
            acc[i] += v
    n = float(len(vecs))
    acc = [v / n for v in acc]
    norm = math.sqrt(sum(v * v for v in acc)) or 1.0
    return [v / norm for v in acc]


def _semantic_params(threshold: float | None) -> tuple[float, int, int]:
    spec = load_rag_spec().get("semantic") or {}
    pct = spec.get("percentile", 0.75)
    if threshold is not None:
        try:
            pct = float(threshold)
        except (TypeError, ValueError):
            pct = float(pct)
    # values in (1, 100] are treated as a percent
    if pct > 1.0:
        pct = pct / 100.0
    min_tok = int(spec.get("minTokens") or 4)
    max_tok = int(spec.get("maxTokens") or 80)
    return pct, min_tok, max_tok


def _sentence_vecs(sents: list[str], embed=None) -> list[list[float]]:
    if embed is not None:
        try:
            return list(embed(sents))
        except Exception:
            pass
    return [hash_embed(s) for s in sents]


def chunk_semantic(text: str, threshold: float | None = None, embed=None) -> list[dict]:
    """Break on consecutive embedding-distance peaks (Kamradt / LlamaIndex)."""
    sents = sentences(text)
    if not sents:
        return []
    if len(sents) == 1:
        return [_unit(sents[0], "semantic", score=1.0)]
    pct, min_tok, max_tok = _semantic_params(threshold)
    embs = _sentence_vecs(sents, embed)
    gaps = [1.0 - cosine(embs[i], embs[i + 1]) for i in range(len(sents) - 1)]
    cut = _percentile(gaps, pct)
    groups: list[list[int]] = [[0]]
    scores: list[float] = []
    for i, gap in enumerate(gaps):
        group = groups[-1]
        tokens_now = len(tokenize(" ".join(sents[j] for j in group)))
        tokens_next = tokens_now + len(tokenize(sents[i + 1]))
        must_split = tokens_next > max_tok
        may_split = gap > cut and tokens_now >= min_tok
        if must_split or may_split:
            scores.append(1.0 - gap)
            groups.append([i + 1])
        else:
            group.append(i + 1)
    scores.append(1.0)
    out = []
    for idxs, score in zip(groups, scores):
        body = " ".join(sents[j] for j in idxs)
        intra = 1.0
        if len(idxs) > 1:
            pairs = [cosine(embs[idxs[a]], embs[idxs[a + 1]]) for a in range(len(idxs) - 1)]
            intra = sum(pairs) / len(pairs)
        out.append(_unit(body, "semantic", score=round(float(intra if len(idxs) > 1 else score), 6)))
    return out


def _has_cue(text: str, cues: tuple[str, ...]) -> str:
    low = f" {text.casefold()} "
    for cue in cues:
        needle = f" {cue} "
        if needle in low or low.strip().startswith(cue + " ") or low.strip().startswith(cue + ","):
            return cue
    return ""


def _split_intra(sent: str) -> list[dict]:
    patterns = load_rag_spec().get("intraSentence") or []
    ordered = sorted(patterns, key=lambda p: len(str(p.get("pattern") or "")), reverse=True)
    low = sent.casefold()
    for row in ordered:
        cue = str(row.get("pattern") or "")
        if not cue:
            continue
        idx = low.find(f" {cue} ")
        head = False
        if idx < 0 and low.startswith(cue + " "):
            idx = 0
            head = True
        if idx < 0:
            continue
        if head:
            rest = sent[len(cue) :].lstrip(" ,")
            if not rest:
                continue
            if "," in rest:
                first, second = rest.split(",", 1)
                first, second = first.strip(), second.strip()
                if first and second:
                    if second.casefold().startswith("then "):
                        second = second[5:].strip()
                    return [
                        _unit(first, "causal", cue=cue, role=str(row.get("left") or "condition")),
                        _unit(second, "causal", cue=cue, role=str(row.get("right") or "effect")),
                    ]
            return [
                _unit(rest, "causal", cue=cue, role=str(row.get("right") or _cue_role(cue))),
            ]
        left = sent[:idx].strip(" ,")
        right = sent[idx + len(cue) + 1 :].strip(" ,")
        if not left or not right:
            continue
        return [
            _unit(left, "causal", cue=cue, role=str(row.get("left") or "residual")),
            _unit(right, "causal", cue=cue, role=str(row.get("right") or _cue_role(cue))),
        ]
    cue = _has_cue(sent, causal_cues())
    role = _cue_role(cue) if cue else "residual"
    return [_unit(sent, "causal", cue=cue, role=role)]


def chunk_causal(text: str) -> list[dict]:
    sents = sentences(text)
    if not sents:
        return []
    out: list[dict] = []
    for sent in sents:
        out.extend(_split_intra(sent))
    return out


def chunk_hier(text: str) -> list[dict]:
    sents = sentences(text)
    body = " ".join(sents) or (text or "").strip()
    rows = [_unit(body, "parent", parent=None)]
    for sent in sents:
        rows.append(_unit(sent, "child", parent=0))
    return rows or [_unit(body, "parent")]


def chunk_prop(text: str) -> list[dict]:
    rows = []
    for sent in sentences(text):
        parts = [p.strip() for p in _CLAUSE_SPLIT.split(sent) if p.strip()]
        for part in parts or [sent]:
            rows.append(_unit(part, "prop", role="proposition"))
    return rows


def chunk_late(text: str) -> list[dict]:
    body = (text or "").strip()
    rows = [_unit(body, "late-doc", parent=None)]
    for sent in sentences(text):
        rows.append(_unit(sent, "late-sent", parent=0))
    return rows or [_unit(body, "late-doc")]


def chunk_topic(text: str, threshold: float | None = None, embed=None) -> list[dict]:
    spec = load_rag_spec().get("topic") or {}
    cut = spec.get("threshold", 0.2)
    if threshold is not None:
        try:
            cut = float(threshold)
        except (TypeError, ValueError):
            pass
    max_tok = int(spec.get("maxTokens") or 64)
    sents = sentences(text)
    if not sents:
        return []
    groups: list[list[str]] = [[sents[0]]]
    scores: list[float] = [1.0]
    vecs = {sents[0]: _sentence_vecs([sents[0]], embed)[0]}
    centroid = vecs[sents[0]]
    for sent in sents[1:]:
        cur = _sentence_vecs([sent], embed)[0]
        vecs[sent] = cur
        sim = cosine(centroid, cur)
        nxt = groups[-1] + [sent]
        toks = len(tokenize(" ".join(nxt)))
        if sim < float(cut) or toks > max_tok:
            groups.append([sent])
            scores.append(round(float(sim), 6))
            centroid = cur
        else:
            groups[-1].append(sent)
            centroid = _mean_norm([vecs.get(s) or _sentence_vecs([s], embed)[0] for s in groups[-1]])
    return [_unit(" ".join(g), "topic", score=scores[i], role="topic") for i, g in enumerate(groups)]


def chunk_discourse(text: str) -> list[dict]:
    cues = causal_taxonomy().get("contrast") or ("however", "although", "whereas")
    cues = tuple(sorted(cues, key=len, reverse=True))
    out: list[dict] = []
    for sent in sentences(text):
        cue = _has_cue(sent, cues)
        if not cue:
            out.append(_unit(sent, "discourse", role="claim"))
            continue
        low = sent.casefold()
        idx = low.find(cue)
        left = sent[:idx].strip(" ,")
        right = sent[idx + len(cue) :].strip(" ,")
        if left:
            out.append(_unit(left, "discourse", cue=cue, role="claim"))
        if right:
            out.append(_unit(right, "discourse", cue=cue, role="contrast"))
        elif not left:
            out.append(_unit(sent, "discourse", cue=cue, role="contrast"))
    return out


def chunk_event(text: str) -> list[dict]:
    cues = causal_taxonomy().get("temporal") or ("after", "before", "then", "first", "finally")
    cues = tuple(sorted(cues, key=len, reverse=True))
    sents = sentences(text)
    if not sents:
        return []
    groups: list[list[str]] = [[sents[0]]]
    hits = [_has_cue(sents[0], cues)]
    for sent in sents[1:]:
        cue = _has_cue(sent, cues)
        if cue:
            groups.append([sent])
            hits.append(cue)
        else:
            groups[-1].append(sent)
    return [
        _unit(" ".join(g), "event", cue=hits[i], role="event", score=float(i))
        for i, g in enumerate(groups)
    ]


STRATEGIES = {
    "fixed": chunk_fixed,
    "token": chunk_token,
    "sentence": chunk_sentence,
    "window": chunk_window,
    "recursive": chunk_recursive,
    "semantic": chunk_semantic,
    "causal": chunk_causal,
    "hier": chunk_hier,
    "prop": chunk_prop,
    "late": chunk_late,
    "topic": chunk_topic,
    "discourse": chunk_discourse,
    "event": chunk_event,
}


def chunk_text(text: str, strategy: str, **args) -> list[dict]:
    fn = STRATEGIES.get(strategy)
    if not fn:
        raise KeyError(strategy)
    if strategy in {"fixed", "token", "window", "recursive"}:
        return fn(text, int(args.get("n") or 12))
    if strategy in {"semantic", "topic"}:
        thresh = args.get("threshold")
        if thresh is None:
            thresh = args.get("percentile")
        return fn(text, thresh, embed=args.get("embed"))
    return fn(text)
