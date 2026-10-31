"""Deterministic number checking: every number in a text must come from the evidence.

Adapted from agentville (hammadtq/agentville), src/agentville/verify.py `check_text`. The Urdu and
Hindi date handling is cut; the rule is stricter: agentville let small integers (<= 10) through as
"unchecked", but here a minute count of 5 is exactly the kind of number that must be right, so the
only number allowed without evidence is the election year.

A number matches a value if it equals the value rounded to the precision the text uses ("7.5"
matches 7.5, "7" matches 7.2). Calendar dates and clock times are removed first; the post guard
checks clock times itself.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MONTHS = (r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
          r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?")
_DATE_PATTERNS = [
    (r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.+-]+)?\b", re.IGNORECASE),
    (r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + MONTHS, 0),
    (MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?\b", 0),
    (r"\b\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?(?![a-z])", re.IGNORECASE),  # 9:42pm, 9 p.m.
    (r"\b\d{1,2}:\d{2}\b", 0),
    (r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", 0),
]
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent|x\b|-fold|fold)?", re.IGNORECASE)
ALLOWED_YEARS = {2026.0}


@dataclass
class Number:
    raw: str
    value: float
    decimals: int
    kind: str  # count | percent | ratio


@dataclass
class TextCheck:
    supported: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported


def strip_dates(text: str) -> str:
    for p, flags in _DATE_PATTERNS:
        text = re.sub(p, " ", text, flags=flags)
    return text


def numbers_in(text: str) -> list[Number]:
    out = []
    for m in _NUMBER.finditer(strip_dates(text)):
        whole, frac, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        value = float(whole.replace(",", "") + ("." + frac if frac else ""))
        kind = "percent" if unit in ("%", "percent") else "ratio" if unit else "count"
        out.append(Number(m.group(0).strip(), value, len(frac or ""), kind))
    return out


def numeric_values(data: Any) -> list[float]:
    vals: list[float] = []
    if isinstance(data, bool):
        return vals
    if isinstance(data, (int, float)):
        vals.append(float(data))
    elif isinstance(data, dict):
        for v in data.values():
            vals += numeric_values(v)
    elif isinstance(data, (list, tuple)):
        for v in data:
            vals += numeric_values(v)
    return vals


def matches(n: Number, values: Iterable[float]) -> bool:
    tol = 0.5 * 10 ** -n.decimals + 1e-9
    return any(abs(abs(v) - n.value) <= tol for v in values)  # "5 min behind" may cite a lead of -5


def check_text(text: str, evidence: Any) -> TextCheck:
    values = numeric_values(evidence)
    res = TextCheck()
    for n in numbers_in(text):
        if n.kind == "count" and n.decimals == 0 and n.value in ALLOWED_YEARS:
            res.supported.append(n.raw)
        elif n.kind == "count" and matches(n, values):
            res.supported.append(n.raw)
        else:
            res.unsupported.append(n.raw)
    return res
