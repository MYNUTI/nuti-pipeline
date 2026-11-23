"""content_hash — build.py · Java ContentHashCalculator 와 같은 값.

- 30칸(HASH_COLUMNS)을 0x1F 로 이어 붙여 UTF-8 sha256 hex. None 은 빈 문자열.
- 숫자: 소수 둘째 자리 HALF_EVEN, **float 의 실제 2진 값 기준** (= Python round(x, 2)).
  끝의 0 · 점 제거, -0 → 0, 지수 표기 없음.
  Decimal(str(x)) 로 반올림하면 .xx5 경계에서 틀어진다 (2.675 → 2.68 이 되어버림).
- 참/거짓: true / false.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

from .columns import HASH_COLUMNS

SEPARATOR = "\x1f"
_CENT = Decimal("0.01")


def format_number(value: float | int | Decimal | None) -> str:
    if value is None:
        return ""
    f = float(value)  # Decimal 도 float 2진 값으로 바꿔서 맞춘다 (Java formatNumber(BigDecimal) 과 같음)
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"숫자가 아닙니다: {value!r}")
    d = Decimal(f).quantize(_CENT, rounding=ROUND_HALF_EVEN)
    if d == 0:
        return "0"
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_bool(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def compute(row: Mapping[str, str | None]) -> str:
    """이미 문자열로 맞춘 값(format_number · format_bool)의 행 → hash. 없는 칸 · None 은 빈 문자열."""
    joined = SEPARATOR.join((row.get(c) or "") for c in HASH_COLUMNS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
