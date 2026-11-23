"""BrandNameRulesTest 이식 — brand.md 「함수 정의」 · 「회사 이름 정리」, 조회표 없이 문자열 규칙만."""
from __future__ import annotations

import pytest

from nuti_pipeline.brand.names import (NameRules, affiliate, compact, core, merge1, null_if_maker, same, strip_foreign,
                                       tokens)

# 시도 + 예시에 쓰는 시 · 군 지명. 실제로는 I0300 · I1420 SITE_ADDR 에서 뽑아 더한다
RULES = NameRules({"서울", "경기", "원주", "천안", "익산", "서천"})


@pytest.mark.parametrize("raw, expected", [
    ("(주)빙그레", "빙그레"),
    ("㈜에스피씨삼립", "에스피씨삼립"),
    ("롯데제과주식회사", "롯데제과"),
    ("농업회사법인 주식회사 코리아식품", "코리아식품"),
    ("주식회사신세계푸드천안공장", "신세계푸드"),
    ("(주)현대그린푸드 스마트푸드센터", "현대그린푸드"),
    ("(주)오뗄 포천용정지점", "오뗄"),
    ("(주)이연에프엔씨 오송공장", "이연에프엔씨"),
    ("크리스피크림원주무실점", "크리스피크림"),
    ("우양서천공장", "우양"),
    ("제이앤제이 제2공장", "제이앤제이"),
    ("(주)내담(NAEDAM F&B)", "내담"),
    ("씨제이제일제당(주)진천 BLOSSOM CAMPUS", "씨제이제일제당 진천 BLOSSOM CAMPUS"),
    ("MORINAGA & CO.,LTD. OYAMA FACTORY", "MORINAGA"),
    ("MILUPAGMBH", "MILUPA"),
    ("NESTLE S.A.", "NESTLE"),
    ("KELSENGROUPA/S", "KELSENGROUP"),
    ("M/S ABC FOODS", "ABC FOODS"),
    ("가나식품/다라유통", "가나식품"),
])
def test_회사_이름_정리(raw, expected):
    assert RULES.clean(raw) == expected


def test_정리하고_비면_원문():
    assert RULES.clean("주식회사") == "주식회사"


def test_지명_목록에_없는_지명은_떼지_않는다():
    provinces_only = NameRules()  # 시도 17개만
    assert provinces_only.clean("주식회사신세계푸드천안공장") == "신세계푸드천안"  # 끝의 「공장」만 뗌
    assert provinces_only.clean("크리스피크림원주무실점") == "크리스피크림원주무실점"


def test_해외_법인_꼬리_2차():
    assert strip_foreign("PT. INDOFOOD SUKSES MAKMUR TBK") == "INDOFOOD SUKSES MAKMUR"
    assert strip_foreign("BARILLA G. E R. FRATELLI SPA") == "BARILLA G. E R. FRATELLI"
    assert strip_foreign("JOHNSON & CO") == "JOHNSON & CO"   # 「& CO」는 남김
    assert strip_foreign("ABC CO") == "ABC"                   # 맨 끝 CO 는 뗌
    assert strip_foreign("AB") == "AB"


def test_compact는_소문자_한글_영숫자_앤드_플러스만():
    assert compact("동원F&B") == "동원f&b"
    assert compact("혜자)제육김치볶음밥") == "혜자제육김치볶음밥"
    assert compact(None) == ""


def test_낱말_나누기():
    assert tokens("[피코크]초코칩 쿠키_파티") == ["피코크", "초코칩", "쿠키", "_", "파티"]
    assert tokens("피자_ 콤비네이션") == ["피자", "_", "콤비네이션"]
    assert tokens("A/B·C,D") == ["A", "B", "C", "D"]
    assert tokens(None) == []


def test_일단계_합치기():
    assert merge1("씨제이제일제당") == "CJ제일제당"
    assert merge1("롯데제과") == "롯데웰푸드"
    assert merge1("에스피씨삼립") == "SPC삼립"
    assert merge1("한국야쿠르트") == "hy"
    assert merge1("빙그레") == "빙그레"
    assert merge1("빙그레푸드") == "빙그레푸드"  # 「같음」 방식은 정확히 같을 때만


def test_꼬리_떼기와_같은_회사():
    assert core("신세계푸드") == "신세계"
    assert core("오복식품") == "오복"
    assert core("팜") == "팜"  # 떼면 2글자 미만 — 그대로
    assert same("남양유업", "남양")
    assert same("신세계푸드", "신세계푸드")
    assert not same("신세계푸드", "이마트")
    assert not same(None, "이마트")


def test_계열_5쌍():
    assert affiliate("해태아이스크림", "빙그레")
    assert affiliate("비락", "hy")
    assert not affiliate("빙그레", "해태아이스크림")  # 방향이 있다 (회사, 바코드 주인)


def test_제조_값은_None():
    assert null_if_maker("제조 신세계푸드") is None
    assert null_if_maker("") is None
    assert null_if_maker(None) is None
    assert null_if_maker("롯데") == "롯데"
