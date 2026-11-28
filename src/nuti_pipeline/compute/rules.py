"""34컬럼 계산의 칸 단위 규칙 (Java ComputeRules · BarcodeNormalizer). 순수 함수 — tests/test_compute_rules.py 로 고정."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from ..content_hash import format_number as _fmt
from ..jtext import jstrip, jtrim, parse_double

A = re.ASCII


# ── 공통 ──────────────────────────────────────────────────────────────────

def text(s: str | None) -> str | None:
    """앞뒤 공백을 떼고, 비면 None."""
    if s is None:
        return None
    t = jstrip(s)
    return t or None


def number(s: str | None) -> float | None:
    """숫자 문자열 → float. 비었거나 숫자가 아니면 None. 천 단위 쉼표는 뗀다 (영양성분DB 에 "1,670.000" 같은 값이 있음)."""
    t = text(s)
    if t is None:
        return None
    return parse_double(t.replace(",", ""))


def format_number(d: float | None) -> str | None:
    """content_hash 와 같은 숫자 표기. None 은 None."""
    return None if d is None else _fmt(d)


_WS = re.compile(r"\s+", A)
_NON_DIGIT = re.compile(r"\D", A)


def norm(s: str | None) -> str:
    """비교용 정규화 — NFKC, 소문자, 공백 하나로."""
    t = text(s)
    if t is None:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", t).lower())


def digits(s: str | None) -> str:
    """날짜 비교용 — 숫자만 남긴다 (2026-11-15 · 20261115 를 같게)."""
    t = text(s)
    return "" if t is None else _NON_DIGIT.sub("", t)


# ── report_no ─────────────────────────────────────────────────────────────

_REPORT_NO = re.compile(r"\d{11,16}", A)
_SPACE_HYPHEN = re.compile(r"[\s-]", A)


def report_no(raw: str | None) -> str | None:
    """「/」로 나눈 첫 번째에서 공백 · 하이픈을 빼고 숫자 11~16자리만. 아니면 None (수입식품 내부 코드 등)."""
    t = text(raw)
    if t is None:
        return None
    first = _SPACE_HYPHEN.sub("", t.split("/", 1)[0])
    return first if _REPORT_NO.fullmatch(first) else None


# ── barcode ───────────────────────────────────────────────────────────────

def _valid_gtin14(d: str) -> bool:
    """GTIN-14 체크섬: 왼쪽부터 13자리에 3,1 가중치 교대 합 → (10 − 합 mod 10) mod 10 == 14번째 자리."""
    if len(d) != 14 or not d.isascii() or not d.isdigit():
        return False
    s = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(d[:13]))
    return (10 - s % 10) % 10 == int(d[13])


def normalize_barcode(raw: str | None) -> str | None:
    """8 · 12 · 13 · 14자리 → 14자리 (Java BarcodeNormalizer.tryNormalize). 숫자 외 글자가 섞이거나 체크숫자가 틀리면 None.

    주의: HACCP 처럼 "8 809678 470007" 로 공백이 섞인 값은 Java 와 같게 None 이 된다.
    """
    if raw is None:
        return None
    s = jtrim(raw)
    if not s or not s.isascii() or not s.isdigit() or len(s) not in (8, 12, 13, 14):
        return None
    d = s.rjust(14, "0")
    return d if _valid_gtin14(d) else None


def gtin14(raw: str | None) -> str | None:
    """원천 바코드 → 14자리 GTIN. 체크숫자가 맞아야 하고 아래는 뺀다.
    - 묶음 인디케이터: 14자리 맨 앞이 0 이 아님 (박스 단위 코드)
    - 매장 코드: EAN-13 이 2 로 시작(20~29), UPC-12 가 2 · 4 로 시작(02 · 04)
    """
    t = text(raw)
    if t is None:
        return None
    d = normalize_barcode(t)
    if d is None or d[0] != "0":
        return None
    body = d[1:]
    if body.startswith(("2", "02", "04")):
        return None
    return d


# ── serving_size ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Amount:
    """총내용량. unit 은 g 또는 mL."""
    value: float
    unit: str

    def format(self) -> str:
        """34컬럼 표기 — 300g, 2450g, 90mL, 9.5g."""
        return _fmt(self.value) + self.unit


_AMOUNT = re.compile(r"^(\d+(?:\.\d+)?)\s*(kg|g|ml|l)$", re.ASCII | re.IGNORECASE)


def amount(raw: str | None) -> Amount | None:
    """「80.00g」 「2.2kg」 「900mL」 형태만 받는다. kg → g, L → mL. 묶음 표기(×, *, x)나 다른 모양은 None.
    천 단위 쉼표는 뗀다 (영양성분DB Z10500 에 "1,200.000g" 같은 값이 있음)."""
    t = text(raw)
    if t is None:
        return None
    t = unicodedata.normalize("NFKC", t).replace(" ", "").replace(",", "")
    m = _AMOUNT.match(t)
    if not m:
        return None
    v = float(m.group(1))
    if v <= 0:
        return None
    u = m.group(2).lower()
    if u == "kg":
        return Amount(v * 1000, "g")
    if u == "g":
        return Amount(v, "g")
    if u == "l":
        return Amount(v * 1000, "mL")
    return Amount(v, "mL")


def amount_of(number_: str | None, unit: str | None) -> Amount | None:
    """숫자와 단위가 따로 온 경우 (푸드QR 영양 표시 기준량 TCT + TCD)."""
    n, u = text(number_), text(unit)
    if n is None or u is None:
        return None
    return amount(n + u)


# ── 영양성분 ──────────────────────────────────────────────────────────────

NUTRIENTS = 10

# 영양성분DB 필드 — 열량 · 나트륨 · 탄수화물 · 당류 · 지방 · 트랜스지방 · 포화지방 · 콜레스테롤 · 식이섬유 · 단백질 (100g 당)
NUTRI_DB_FIELDS: tuple[str, ...] = (
    "AMT_NUM1", "AMT_NUM13", "AMT_NUM6", "AMT_NUM7", "AMT_NUM4",
    "AMT_NUM25", "AMT_NUM24", "AMT_NUM23", "AMT_NUM8", "AMT_NUM3",
)

# 푸드QR 영양 항목 이름(NIRWMT_NM, 괄호 · 공백을 뗀 값) → 10종 순서 번호
_FOODQR_NUTRIENT: dict[str, int] = {
    "열량": 0, "에너지": 0, "나트륨": 1, "탄수화물": 2, "당류": 3, "총당류": 3,
    "지방": 4, "총지방": 4, "트랜스지방": 5, "포화지방": 6, "콜레스테롤": 7, "식이섬유": 8, "단백질": 9,
}
_PAREN = re.compile(r"[(\[（].*?[)\]）]", A)
_ANY_WS = re.compile(r"\s", A)


def foodqr_nutrient_index(name: str | None) -> int:
    """푸드QR 영양 항목 이름 → 순서 번호(0~9). 10종이 아니면 -1."""
    t = text(name)
    if t is None:
        return -1
    t = _ANY_WS.sub("", _PAREN.sub("", unicodedata.normalize("NFKC", t)))
    return _FOODQR_NUTRIENT.get(t, -1)


class Basis(str, Enum):
    """푸드QR 영양 표시 기준 (NTRTN_INDCT_CD)."""
    PER_100 = "PER_100"
    TOTAL = "TOTAL"
    UNIT = "UNIT"


def basis(code: str | None, basis_amount: float | None) -> Basis:
    """「100g당 · 100ml당」 = PER_100, 「총 내용량당」 = TOTAL, 「단위 내용량당 · 1회 제공량당」 = UNIT.
    코드에 이 글자가 없으면 기준량(TCT)이 100 이면 PER_100, 아니면 TOTAL."""
    t = text(code)
    if t is not None:
        c = t.replace(" ", "")
        if "단위" in c or "1회" in c or "회제공" in c:
            return Basis.UNIT
        if "총" in c:
            return Basis.TOTAL
        if "100" in c:
            return Basis.PER_100
    return Basis.PER_100 if basis_amount is not None and basis_amount == 100.0 else Basis.TOTAL


def per100(value: float, b: Basis, amt: float | None) -> float | None:
    """푸드QR 표시값 → 100g 당. amt 는 TOTAL 이면 총내용량, UNIT 이면 단위 내용량. 모르면 None."""
    if b is Basis.PER_100:
        return value
    return None if amt is None or amt <= 0 else value * 100 / amt


def whole(p100: float | None, serving: Amount | None) -> float | None:
    """한 봉지 전체 = 100g 당 × 총내용량 / 100. 둘 중 하나라도 없으면 None."""
    if p100 is None or serving is None:
        return None
    return p100 * serving.value / 100


# ── 지명 (brand 조회표 7) ─────────────────────────────────────────────────

def place_name(second_word: str | None) -> str | None:
    """주소 둘째 낱말(예: 포천시)에서 끝의 시 · 군 · 구를 뗀 값. 2글자 미만이거나 한글이 아니면 None."""
    t = text(second_word)
    if t is None:
        return None
    t = unicodedata.normalize("NFKC", t)
    if len(t) > 1 and t[-1] in "시군구":
        t = t[:-1]
    return t if len(t) >= 2 and all("가" <= c <= "힣" for c in t) else None