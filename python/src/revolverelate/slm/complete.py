from __future__ import annotations

import json
import re
import urllib.request

from revolverelate.errors import AskError
from revolverelate.slm.probe import SlmHandle, probe_slm


def complete(prompt: str, *, system: str, handle: SlmHandle | None = None, timeout: float = 180.0) -> str:
    slm = handle or probe_slm()
    if not slm.available or slm.kind == "none":
        raise AskError(slm.reason or "No SLM available")
    url = slm.base_url.rstrip("/")
    if slm.kind == "ollama":
        url += "/api/chat"
        payload = {
            "model": slm.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0.1},
        }
        headers = {"Content-Type": "application/json"}
    else:
        url += "/chat/completions"
        payload = {
            "model": slm.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        headers = {"Content-Type": "application/json"}
        if slm.api_key:
            headers["Authorization"] = f"Bearer {slm.api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if slm.kind == "ollama":
        return str((body.get("message") or {}).get("content") or "")
    choices = body.get("choices") or []
    return str(((choices[0].get("message") or {}).get("content") if choices else "") or "")


def extract_json(text: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise AskError("SLM did not return JSON")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise AskError("SLM JSON must be an object")
    return data
