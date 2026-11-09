"""Parse Kubernetes resource quantities ("250m", "1.5", "512Mi", "1G", "2e3")."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_DECIMAL = {
    "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"), "": Decimal(1),
    "k": Decimal("1e3"), "M": Decimal("1e6"), "G": Decimal("1e9"), "T": Decimal("1e12"),
    "P": Decimal("1e15"), "E": Decimal("1e18"),
}
_PATTERN = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([eE][+-]?\d+|Ki|Mi|Gi|Ti|Pi|Ei|[numkMGTPE])?$")


def parse_quantity(value: str | int | float) -> Decimal:
    """Return the quantity in base units (cores for CPU, bytes for memory)."""
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    match = _PATTERN.match(text)
    if not match:
        raise ValueError(f"not a Kubernetes quantity: {value!r}")
    number, suffix = match.group(1), match.group(2) or ""
    try:
        base = Decimal(number)
    except InvalidOperation as exc:  # pragma: no cover - the regex already guards this
        raise ValueError(f"not a Kubernetes quantity: {value!r}") from exc
    if suffix in _BINARY:
        return base * _BINARY[suffix]
    if suffix[:1] in "eE" and len(suffix) > 1:
        return base * (Decimal(10) ** int(suffix[1:]))
    return base * _DECIMAL[suffix]
