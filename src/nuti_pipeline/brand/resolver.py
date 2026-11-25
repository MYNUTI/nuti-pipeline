"""묶음 하나의 brand 칸 계산 (Java BrandResolver · BrandResolvers, brand.md 「계산 순서」). AI 없이 조회표 + 문자열 규칙으로만 정한다.

- 영양성분DB 행(source = nutri_db)용. 묶음 단위로 정하고, 묶음 안 모든 행에 같은 값을 넣는다 (compute/master.py).
- 앞 단계에서 값이 정해지면 거기서 끝난다.
- 브랜드로 확인되지 않으면 None — 마스터의 「제조 ○○」는 넣지 않는다.
  틀리면 「브랜드인데 None」 방향이어야 한다 (없는 브랜드를 지어내지 않음).

계산 순서
  1. 11-12 수동 고침 (빈 값 · 「제조 ○○」면 None)
  2. 쿠팡 보정 고정값 (합치기 안 거침)
  3. 제품명 첫 낱말 → 브랜드 사전 (메뉴 머리말 「피자_ …」이면 건너뜀)
  4. 회사 이름 고르기: 판매원 > 제조사 > 수입 → 정리(clean) → 1단계 합치기
  5. 하위 브랜드 (UNIQUE / 회사별 SUB)
     제조사 · 수입이면 해외 법인 꼬리 2차, SPC삼립 → 삼립
  6. 판매원이면 끝
  7. 제조사 · 수입: 바코드 주인이 다른 회사면 None → 회사 이름표 (믿을 만한 정한 곳만)
     브랜드 · 유통사 → 채택 / 둘 다 → 제품명 · 쿠팡 상품명에 회사 핵심 이름이 있거나 바코드 주인이 같을 때만 / 그 밖 → None
  (채택하는 값은 모두 2단계 합치기를 거친다)
"""
from __future__ import annotations

from collections.abc import Iterable

from ..compute.master import BrandInput
from ..jtext import jstrip
from . import names
from .names import PROVINCES, NameRules
from .tables import LABEL_BOTH, LABEL_BRAND, LABEL_RETAILER, BrandTables

BRAND_LABELS = frozenset({LABEL_BRAND, LABEL_RETAILER})


class TablesNotReady(RuntimeError):
    pass


def rules_with_places(extra_place_names: Iterable[str | None]) -> NameRules:
    """시도 17개 + I0300 SITE_ADDR 에서 뽑은 지명 (2글자 이상)."""
    places = set(PROVINCES)
    for p in extra_place_names:
        if p is not None and len(jstrip(p)) >= 2:
            places.add(jstrip(p))
    return NameRules(places)


def _not_blank(s: str | None) -> bool:
    return s is not None and jstrip(s) != ""


class BrandResolver:
    def __init__(self, tables: BrandTables, rules: NameRules | None = None) -> None:
        self.tables = tables
        self.rules = rules or NameRules()

    @property
    def ready(self) -> bool:
        return self.tables.ready

    def resolve(self, i: BrandInput) -> str | None:
        """brand 칸 값. 브랜드로 확인되지 않으면 None. 조회표가 준비되지 않았으면 TablesNotReady."""
        t = self.tables
        if not t.ready:
            raise TablesNotReady(f"브랜드 조회표가 준비되지 않았습니다: {t.problem}")
        g = i.group_key

        # 1. 11-12 수동 고침 — 빈 값이나 「제조 ○○」면 None
        fixed = t.fix1112(g)
        if fixed is not None:
            return names.null_if_maker(fixed)

        # 2. 쿠팡 보정 — 고정값 그대로 (합치기 안 거침)
        coupang = t.coupang_fix(g)
        if coupang is not None:
            return coupang

        # 3. 제품명 첫 낱말 → 브랜드 사전 (메뉴 머리말 「피자_ …」이면 건너뜀)
        tk = names.tokens(i.product_name)
        w = names.compact(tk[0]) if tk else ""
        menu_head = len(tk) > 1 and tk[1] == "_"
        if not menu_head:
            b = t.dictionary_lookup(w)
            if b is not None:
                return t.merge2(b)

        # 4. 회사 이름 고르기: 판매원 > 제조사 > 수입 → 정리 → 1단계 합치기
        if _not_blank(i.seller):
            src, raw = "판매원", i.seller
        elif _not_blank(i.maker):
            src, raw = "제조사", i.maker
        elif _not_blank(i.importer):
            src, raw = "수입", i.importer
        else:
            return None
        c = self.rules.clean(raw)
        v = jstrip(raw) if not c else names.merge1(c)

        # 5. 하위 브랜드
        unique = names.UNIQUE.get(w)
        if unique is not None:
            return t.merge2(unique)
        if w in names.SUB.get(v, ()):
            return t.merge2(w)

        if src != "판매원":
            v = names.strip_foreign(v)  # 해외 법인 꼬리 2차 (제조사 · 수입만)
        if v == "SPC삼립":
            v = "삼립"                   # apply_v3 SAME — 이름표 키도 「삼립」

        # 6. 판매원은 이름표 · 바코드를 보지 않는다
        if src == "판매원":
            return t.merge2(v)

        # 7. 제조사 · 수입: 바코드 주인 → 회사 이름표
        owner = i.barcode_owner
        if owner is not None and not names.same(v, owner) and not names.affiliate(v, owner):
            return None  # 남의 제품을 만든 회사
        label = t.company_label(v)
        if label is None or not label.trusted:
            return None  # 표에 없는 새 회사 · 믿을 만하지 않은 판정
        if label.label in BRAND_LABELS:
            return t.merge2(v)
        if label.label == LABEL_BOTH:
            k = names.core(v)
            if len(k) >= 2 and (k in names.compact(i.product_name) or k in names.compact(t.coupang_name(g))):
                return t.merge2(v)
            if owner is not None and names.same(v, owner):
                return t.merge2(v)
        return None  # 제조 전문 · 업소·원료 · 미확인 · 「둘 다」인데 자기 제품이라는 신호가 없음