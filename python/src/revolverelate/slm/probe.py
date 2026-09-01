"""Hardware-first SLM probe, then cloud OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SlmHandle:
    kind: str
    model: str
    base_url: str
    reason: str
    available: bool = False
    tags: list[str] = field(default_factory=list)
    api_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "base_url": self.base_url,
            "reason": self.reason,
            "available": self.available,
            "tags": list(self.tags)[:12],
        }


def slm_wanted() -> bool:
    flag = (os.environ.get("REVOLVERELATE_SLM") or "auto").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _http_json(url: str, *, data: dict | None = None, timeout: float = 2.0, headers=None) -> Any:
    body = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        raw = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=raw, headers=hdrs, method="POST")
    else:
        req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload.decode("utf-8")) if payload else {}


def _score(name: str) -> tuple:
    low = name.lower()
    if any(x in low for x in ("embed", "bge-", "minilm")):
        return (0, 0)
    size = 0.0
    for token, pts in (("27b", 27), ("14b", 14), ("8b", 8), ("7b", 7), ("3b", 3)):
        if token in low:
            size = pts
            break
    family = 4 if "qwen" in low else 3 if any(x in low for x in ("llama", "mistral", "phi")) else 1
    return (1, family, size)


def _best(tags: list[str]) -> str | None:
    ranked = [t for t in tags if _score(t)[0] > 0]
    if not ranked:
        return None
    ranked.sort(key=_score, reverse=True)
    return ranked[0]


_CACHE: SlmHandle | None = None


def probe_slm(*, force: bool = False) -> SlmHandle:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    if not slm_wanted():
        _CACHE = SlmHandle("none", "", "", "REVOLVERELATE_SLM disables the model", False)
        return _CACHE
    explicit_model = (os.environ.get("REVOLVERELATE_SLM_MODEL") or "").strip()
    ollama = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    if not ollama.startswith("http"):
        ollama = "http://" + ollama
    try:
        data = _http_json(ollama + "/api/tags", timeout=1.2)
        tags = [str(r.get("name")) for r in (data.get("models") or []) if r.get("name")]
        model = explicit_model or _best(tags)
        if model:
            _CACHE = SlmHandle("ollama", model, ollama, "local Ollama", True, tags)
            return _CACHE
    except Exception:
        pass
    for url in (
        os.environ.get("REVOLVERELATE_SLM_BASE_URL") or "",
        "http://127.0.0.1:1234/v1",
    ):
        if not url:
            continue
        try:
            data = _http_json(url.rstrip("/") + "/models", timeout=1.2)
            tags = [str(r.get("id")) for r in (data.get("data") or []) if r.get("id")]
            model = explicit_model or _best(tags) or "local"
            _CACHE = SlmHandle("openai_compat", model, url, "local OpenAI-compatible", True, tags)
            return _CACHE
        except Exception:
            continue
    api_key = (os.environ.get("REVOLVERELATE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    api_base = (os.environ.get("REVOLVERELATE_API_BASE") or "https://api.openai.com/v1").rstrip("/")
    if api_key:
        _CACHE = SlmHandle(
            "cloud",
            explicit_model or os.environ.get("REVOLVERELATE_CLOUD_MODEL") or "gpt-4o-mini",
            api_base,
            "cloud OpenAI-compatible API",
            True,
            api_key=api_key,
        )
        return _CACHE
    _CACHE = SlmHandle("none", "", "", "no local SLM and no REVOLVERELATE_API_KEY", False)
    return _CACHE


def slm_status() -> dict[str, Any]:
    handle = probe_slm()
    data = handle.to_dict()
    data["wanted"] = slm_wanted()
    return data
