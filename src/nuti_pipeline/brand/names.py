"""brand 칸 계산의 문자열 규칙 (Java BrandNameRules, brand.md 「함수 정의」 · 「회사 이름 정리」 · 「작은 표」).

순수 함수 — tests/test_brand_names.py 로 고정. 지명 목록만 인스턴스마다 다르다
(시도 17개가 기본, 수집이 붙으면 I0300 · I1420 SITE_ADDR 지명을 더한다).
정규식은 Java 와 같게 ASCII 기준(re.A)으로 컴파일한다.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ..jtext import jstrip

A = re.ASCII
AI = re.ASCII | re.IGNORECASE

PROVINCES: frozenset[str] = frozenset({
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원",
    "충북", "충남", "전북", "전남", "경북", "경남", "제주",
})

# 하위 브랜드 — 제품명 첫 낱말이 이것이면 누가 만들어도 그 이름. 키는 compact
UNIQUE: dict[str, str] = {
    "피코크": "피코크", "노브랜드": "노브랜드", "유어스": "유어스",
    "헤이루": "헤이루", "heyroo": "헤이루", "세븐셀렉트": "세븐셀렉트",
    "오늘좋은": "오늘좋은", "초이스엘": "초이스엘", "요리하다": "요리하다",
    "심플러스": "심플러스", "simplus": "심플러스", "곰곰": "곰곰",
    "탐사": "탐사", "비비고": "비비고", "햇반": "햇반",
    "청정원": "청정원", "종가집": "종가집", "해찬들": "해찬들",
}

# 하위 브랜드 — 그 회사(1단계 합치기 뒤 이름) 제품일 때만. 낱말은 compact
SUB: dict[str, frozenset[str]] = {
    "CJ제일제당": frozenset({"비비고", "햇반", "백설", "고메", "스팸", "맛밤", "해찬들", "다담", "컵반", "쁘띠첼", "프레시안"}),
    "대상": frozenset({"청정원", "종가", "종가집", "안주야", "호밍스"}),
    "오리온": frozenset({"닥터유", "마켓오"}),
    "매일유업": frozenset({"상하목장"}),
    "동서식품": frozenset({"맥심"}),
    "정식품": frozenset({"베지밀"}),
    "동원F&B": frozenset({"양반", "리챔", "덴마크"}),
}


@dataclass(frozen=True)
class Merge1:
    starts: bool              # True: compact 가 그 글자로 시작 / False: 같을 때만
    keys: tuple[str, ...]
    display: str


# 1단계 합치기 — 적힌 순서대로, 처음 맞는 것에서 멈춘다
MERGE1: tuple[Merge1, ...] = (
    Merge1(True, ("씨제이제일제당", "cj제일제당"), "CJ제일제당"),
    Merge1(True, ("롯데웰푸드", "롯데제과", "롯데푸드"), "롯데웰푸드"),
    Merge1(True, ("동원에프앤비", "동원fb"), "동원F&B"),
    Merge1(True, ("에치와이", "한국야쿠르트"), "hy"),
    Merge1(True, ("해태에이치티비",), "해태htb"),
    Merge1(True, ("에스피씨삼립",), "SPC삼립"),
    *(Merge1(True, (n,), n) for n in (
        "롯데칠성음료", "오뚜기", "삼양식품", "해태아이스크림", "해태제과", "남양유업", "매일유업",
        "서울우유", "풀무원", "크라운제과", "동서식품", "코카콜라음료", "동아오츠카", "하림", "사조", "샘표", "광동제약",
        "웅진식품", "신세계푸드", "아워홈", "크리스피크림")),
    *(Merge1(False, (n,), n) for n in ("농심", "팔도", "빙그레", "대상", "오리온", "일화", "정식품")),
)

# 계열 5쌍 (회사, 바코드 주인) — 같은 그룹이라 남의 제품으로 보지 않는다
AFFILIATES: tuple[tuple[str, str], ...] = (
    ("해태아이스크림", "빙그레"), ("샤니", "삼립"), ("호남샤니", "삼립"), ("비락", "hy"), ("남양유업", "백미당"),
)

# 꼬리 목록 (core) — 긴 것부터. 모두 compact 형태. 같은 길이는 원래 순서 유지(Java 안정 정렬과 같음)
TAILS: tuple[str, ...] = tuple(sorted((
    "종합식품", "식품공업", "식품산업", "식품", "푸드시스템", "푸드", "에프앤비", "엔에프", "f&b", "fnb", "에프엔비", "제과",
    "유업", "농산", "수산", "산업", "코리아", "korea", "인터내셔날", "인터내셔널", "글로벌", "영농조합", "농협", "컴퍼니",
    "상사", "무역", "유통", "바이오", "팜", "제약", "헬스케어", "주류", "음료", "축산", "육가공", "씨푸드", "베이커리",
), key=len, reverse=True))

_NON_COMPACT = re.compile(r"[^0-9a-z가-힣&+]", A)
_LEADING_BRACKET = re.compile(r"^[\[(【<]([^\])】>]{1,20})[\])】>]", A)
_TOKEN_SPLIT = re.compile(r"[\s/·,]+", A)

_KO_LEGAL = re.compile(
    r"학교법인|사회복지법인|재단법인|사단법인|\(주\)|㈜|주식회사|\(유\)|유한책임회사|유한회사|농업회사법인|영농조합법인|어업회사법인"
    r"|\(합\)|합자회사|\(사\)|\(재\)|\(농\)", A)
_PAREN_SITE = re.compile(r"\([^)]*(공장|센터|지점)\)", A)
_TRAILING_EN_PAREN = re.compile(r"\([A-Za-z0-9&.,' -]+\)\s*$", A)
_MULTI_SPACE = re.compile(r"\s+", A)
_LEAD_MS = re.compile(r"^(M/S|A/S)\s*", AI)
_TAIL_AS = re.compile(r"\s*A/S$", AI)
_AND_CO_LTD = re.compile(r"\s*&\s*CO\.?\s*,?\s*LTD.*$", AI)
_FACTORY = re.compile(r"\s*\S*\s+FACTORY\s*$", AI)
_GLUED_LEGAL = re.compile(r"(?<=[A-Za-z]{4})(GMBH|SPA|BV|SAS|LTD)$", AI)
_EN_TAIL = re.compile(
    r"[,\s]+(CO\.?\s*,?\s*LTD|PTY\.?\s*LTD|GMBH\s*&\s*CO\.?\s*KG|CORPORATION|LIMITED|LTD|INC|CORP"
    r"|S\.A\.S|SPRL|S\.R\.L|S\.P\.A|S\.L|S\.A|A\.G|LLC|BVBA|B\.V|KG|OY|AB|AS|NV)\.?$", AI)
_SPACED_FACTORY = re.compile(r"\s+\S*공장$", A)
_SPACED_SITE = re.compile(r"\s+\S*(지점|캠퍼스|사업장|센트럴키친|센터)$", A)
_SPACED_STORE = re.compile(r"\s+\S{2,}점$", A)
_NUMBERED_FACTORY = re.compile(r"(제\d+\S*공장|\d+공장)$", A)
_TRIM_PUNCT = re.compile(r"^[\s,.\-]+|[\s,.\-]+$", A)

# 해외 법인 꼬리 2차 (apply_v3 LEG) — 제조사 · 수입에서 온 값에만
_FOREIGN_HEAD = re.compile(r"^(SIA\s+|PT\.\s*|CV\.\s*)", AI)
_FOREIGN_TAIL = re.compile(
    r"(\s+TBK\b.*"
    r"|\s*-\s*SITE\b.*"
    r"|\s+JOINT\s+STOCK(\s+COMPANY)?"
    r"|\s+ANONIM\s+SIRKETI"
    r"|\s+(SANAYI\s+)?VE\s+TICARET(\s+A\.S\.?)?"
    r"|\s+LIMITED\s+SIRKETI"
    r"|\s+(CO\.?,?\s+)?LIMITED"
    r"|\s+PUBLIC\s+COMPANY"
    r"|\s+SDN\.?\s+BHD\.?"
    r"|\s+BHD\.?"
    r"|\s+PTE\.?"
    r"|\s+PVT\.?"
    r"|\s+LLP"
    r"|\s+S\.A\.U\.?"
    r"|\s+JSC"
    r"|(?<=[A-Za-z]{4})(SRL|SPA)"
    r"|\s+(SRL|SPA)"
    r"|(?<!&)\s+CO\.?)\s*$", AI)


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _trim_punct(s: str) -> str:
    return _TRIM_PUNCT.sub("", s)


def _remove_if_left(s: str, p: re.Pattern, min_len: int) -> str:
    """맞는 부분을 지웠을 때 남는 글자가 min_len 이상일 때만 지운다."""
    m = p.search(s)
    if not m:
        return s
    left = _trim_punct(s[:m.start()] + s[m.end():])
    return left if len(left) >= min_len else s


# ── 기본 함수 ─────────────────────────────────────────────────────────────

def compact(s: str | None) -> str:
    """NFKC → 소문자 → [0-9a-z가-힣&+] 외 전부 제거."""
    if s is None:
        return ""
    return _NON_COMPACT.sub("", _nfkc(s).lower())


def tokens(name: str | None) -> list[str]:
    """제품명 → 낱말. 맨 앞 괄호(안 글자 1~20자)는 괄호만 벗겨 낱말로, _ 는 따로 낱말로, 공백 · / · · · , 로 나눈다."""
    if name is None:
        return []
    s = jstrip(_nfkc(name))
    m = _LEADING_BRACKET.search(s)
    if m:
        s = m.group(1) + " " + s[m.end():]
    s = s.replace("_", " _ ")
    return [t for t in _TOKEN_SPLIT.split(s) if t]


def null_if_maker(s: str | None) -> str | None:
    """빈 값이거나 「제조 ○○」면 None — 마스터의 「제조 ○○」는 우리 쪽에서 null."""
    if s is None or not jstrip(s) or jstrip(s).startswith("제조 "):
        return None
    return jstrip(s)


def merge1(s: str) -> str:
    """1단계 합치기 — 회사 이름표 키가 된다. 맞는 것이 없으면 s 그대로."""
    c = compact(s)
    for m in MERGE1:
        for k in m.keys:
            if (c.startswith(k) if m.starts else c == k):
                return m.display
    return s


def core(s: str | None) -> str:
    """compact 에서 꼬리를 끝에서 최대 3번 뗀다. 떼고 2글자 미만이 되면 멈춘다."""
    c = compact(s)
    for _ in range(3):
        for tail in TAILS:
            if c.endswith(tail) and len(c) - len(tail) >= 2:
                c = c[: len(c) - len(tail)]
                break
        else:
            break
    return c


def same(a: str | None, b: str | None) -> bool:
    """같은 회사인가 — core 가 같거나, 둘 다 2글자 이상이고 한쪽이 다른 쪽에 포함."""
    if a is None or b is None:
        return False
    ca, cb = core(a), core(b)
    if ca == cb:
        return ca != ""
    return len(ca) >= 2 and len(cb) >= 2 and (cb in ca or ca in cb)


def affiliate(v: str | None, owner: str | None) -> bool:
    """계열 5쌍 중 same(v, 회사) and same(owner, 바코드 주인)인 쌍이 있는가."""
    return any(same(v, c) and same(owner, o) for c, o in AFFILIATES)


def strip_foreign(v: str | None) -> str | None:
    """해외 법인 꼬리 2차 — 최대 3번. 결과가 2글자 미만이면 원래 v."""
    if v is None:
        return None
    s = v
    for _ in range(3):
        before = s
        s = _FOREIGN_HEAD.sub("", s, count=1)
        s = _FOREIGN_TAIL.sub("", s, count=1)
        s = _trim_punct(s)
        if s == before:
            break
    return v if len(s) < 2 else s


# ── 회사 이름 정리 (clean) — 지명 목록이 들어가므로 인스턴스 ──────────────

class NameRules:
    def __init__(self, place_names: Iterable[str] = PROVINCES) -> None:
        self.place_names: frozenset[str] = frozenset(place_names)
        alt = "|".join(re.escape(p) for p in sorted((p for p in self.place_names if len(p) >= 2), key=len, reverse=True))
        self._place_store = re.compile(f"({alt})\\S{{0,6}}(지점|점|캠퍼스|센터|사업장)$", A) if alt else None
        self._place_factory = re.compile(f"({alt})공장$", A) if alt else None

    def clean(self, raw: str | None) -> str:
        """brand.md 「회사 이름 정리」 1~5. 결과가 비면 원문(strip)."""
        if raw is None:
            return ""
        s = jstrip(_nfkc(raw))
        # 1. 앞의 M/S · A/S, 끝의 A/S. / 가 있으면 앞부분만
        s = _LEAD_MS.sub("", s, count=1)
        s = _TAIL_AS.sub("", s, count=1)
        if "/" in s:
            s = s[: s.index("/")]
        # 2. 한글 법인 표기 → 공백
        s = _KO_LEGAL.sub(" ", s)
        # 3. 괄호 속 공장 · 센터 · 지점, 끝의 영문 괄호
        s = _PAREN_SITE.sub("", s)
        s = _TRAILING_EN_PAREN.sub("", s)
        s = _trim_punct(_MULTI_SPACE.sub(" ", s))
        # 4. 바뀌지 않을 때까지 최대 3번
        for _ in range(3):
            before = s
            s = _AND_CO_LTD.sub("", s, count=1)
            s = _FACTORY.sub("", s, count=1)
            s = _remove_if_left(s, _GLUED_LEGAL, 3)
            s = _EN_TAIL.sub("", s, count=1)
            s = _remove_if_left(s, _SPACED_FACTORY, 2)
            s = _remove_if_left(s, _SPACED_SITE, 2)
            s = _remove_if_left(s, _SPACED_STORE, 2)
            if self._place_store is not None:
                s = _remove_if_left(s, self._place_store, 2)
            s = _remove_if_left(s, _NUMBERED_FACTORY, 2)
            if self._place_factory is not None:
                s = _remove_if_left(s, self._place_factory, 2)
            if s.endswith("공장") and len(s) > 4:
                s = s[:-2]
            s = _trim_punct(s)
            if s == before:
                break
        # 5. 비면 원문
        return s if s else jstrip(raw)
