from __future__ import annotations

from revolverelate.errors import PolicyError


def assert_capability(policy: dict, cap: str) -> None:
    if cap not in (policy or {}).get("capabilities", []):
        raise PolicyError(f"Policy does not grant {cap}")


def is_sensitive(policy: dict, entity: str, attr: str) -> bool:
    key = f"{entity}.{attr}"
    klass = (policy.get("attributes") or {}).get(key) or (policy.get("attributes") or {}).get(
        key.casefold()
    )
    if klass is None:
        for name, value in (policy.get("attributes") or {}).items():
            if name.casefold() == key.casefold():
                klass = value
                break
    return klass in {"critical", "pii"}


def project_allowed(policy: dict, entity: str, attr: str) -> bool:
    if not is_sensitive(policy, entity, attr):
        return True
    reveal = {x.casefold() for x in policy.get("reveal") or []}
    return f"{entity}.{attr}".casefold() in reveal or "reveal" in (policy.get("capabilities") or [])
