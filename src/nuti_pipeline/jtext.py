"""Java 문자열 동작 맞추기 — Java 구현(= build.py 와 hash 가 맞는 것으로 검증된 코드)과 같은 결과를 내기 위한 작은 도우미.

- Java String.strip() 은 Character.isWhitespace 기준이라 줄바꿈 없는 공백(U+00A0 등)은 떼지 않는다. Python str.strip() 은 뗀다.
- Java 정규식의 \\s \\d \\S 는 ASCII 기준. Python 은 유니코드 기준 → 정규식은 re.ASCII 로 컴파일한다.
- Java Double.parseDouble 은 "1.5f" 를 받고 "1_000" 을 거부한다. Python float 은 반대.
"""
from __future__ import annotations

import re

# Character.isWhitespace: 공백 구분자(Zs/Zl/Zp) 중 줄바꿈 없는 공백 3개 제외 + 제어문자 \t \n \x0b \f \r \x1c-\x1f
_JAVA_WS = "".join(chr(c) for c in (
    *range(0x09, 0x0E), *range(0x1C, 0x21), 0x1680, *range(0x2000, 0x2007), *range(0x2008, 0x200B),
    0x2028, 0x2029, 0x205F, 0x3000,
))

_DOUBLE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[fFdD]?", re.ASCII)


def jstrip(s: str) -> str:
    """Java String.strip()."""
    return s.strip(_JAVA_WS)


def jtrim(s: str) -> str:
    """Java String.trim() — 코드값 0x20 이하를 앞뒤에서 뗀다."""
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


def parse_double(s: str) -> float | None:
    """Java Double.parseDouble — 받을 수 없으면 None. (NaN · Infinity · 16진수 표기는 쓰지 않으므로 받지 않는다)"""
    t = jtrim(s)
    if not _DOUBLE.fullmatch(t):
        return None
    return float(t.rstrip("fFdD"))
