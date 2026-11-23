"""BrandLookupTablesTest · BrandResolverTest 이식 — 실제 조회표(tables/brand/)로 고정한다. 표를 바꾸면 건수도 같이 고친다."""
from __future__ import annotations

import pytest

from nuti_pipeline.brand.resolver import BrandResolver, TablesNotReady, rules_with_places
from nuti_pipeline.brand.tables import LABEL_BOTH, LABEL_BRAND, BrandTables
from nuti_pipeline.compute.master import BrandInput


@pytest.fixture(scope="module")
def tables() -> BrandTables:
    t = BrandTables.load()
    if not t.ready:
        # 조회표는 레포에 올리지 않는다 — 없는 곳(CI 등)에서는 건너뛴다
        pytest.skip(f"브랜드 조회표 없음 (tables/brand/ 또는 BRAND_TABLES_DIR): {t.problem}")
    return t


@pytest.fixture(scope="module")
def resolver(tables) -> BrandResolver:
    return BrandResolver(tables)


# ── 조회표 ──

def test_필수_파일이_없으면_예외_없이_준비_안_됨(tmp_path):
    missing = BrandTables.load(tmp_path / "없는폴더")
    assert not missing.ready
    assert "1_브랜드사전_" in missing.problem
    assert missing.dictionary == {} and missing.company_labels == {}
    assert missing.dictionary_lookup("하림") is None


def test_파일을_모두_읽는다(tables):
    assert tables.ready
    assert tables.problem is None
    assert len(tables.dictionary) == 1407        # 1_브랜드사전_2026-11-09
    assert len(tables.company_labels) == 29546   # 2_회사이름표_2026-10-29 (전체)
    assert len(tables.merge2_map) == 61          # 3_같은회사_합치기표_2026-10-29 (28묶음)
    assert len(tables.coupang_fix_map) == 1059   # 4_쿠팡보정_고정값_2026-11-09


def test_선택_표(tables):
    assert len(tables.fix1112_map) == 2          # 11-12 고침 2줄 (고침 1 · 비움 1)
    assert tables.fix1112("b1b59fb6de275ae3") == "롯데"
    assert tables.fix1112("4f80879f80305f34") == ""
    assert len(tables.coupang_names) == 0        # 아직 헤더만
    assert tables.coupang_name("b1b59fb6de275ae3") == ""


def test_사전은_그대로_있으면_그_브랜드(tables):
    assert tables.dictionary_lookup("오빠닭") == "오빠닭"
    assert tables.dictionary_lookup("하림") == "하림"


def test_붙여_써도_쓰는_낱말은_앞부분만_맞아도_인정(tables):
    assert tables.dictionary_lookup("하림프로틴쿠키") == "하림"   # brand.md 예시


def test_붙여쓰기_N_낱말은_앞부분으로_인정하지_않는다(tables):
    assert tables.dictionary_lookup("롯데") == "롯데"            # 「롯데」는 붙여 쓴 이름에도 씀 = N
    assert tables.dictionary_lookup("롯데샌드") is None


def test_빈_낱말은_없음(tables):
    assert tables.dictionary_lookup("") is None
    assert tables.dictionary_lookup(None) is None


def test_회사_이름표와_믿을_만한_판정(tables):
    bingrae = tables.company_label("빙그레")
    assert bingrae.label == LABEL_BRAND
    assert bingrae.trusted                                        # 10-28 웹 판정
    assert tables.company_label("신세계푸드").label == LABEL_BOTH
    assert not tables.company_label("오복식품").trusted           # 브랜드지만 정한 곳이 「규칙」
    assert tables.company_label("후레쉬퍼스트").label == "제조 전문"
    assert tables.company_label("표에없는새회사") is None


def test_합치기는_정확히_같은_이름만(tables):
    assert tables.merge2("SPC삼립") == "삼립"
    assert tables.merge2("CJ제일제당") == "CJ"
    assert tables.merge2("동원F&B") == "동원"
    assert tables.merge2("해태htb") == "해태htb"   # 다른 회사라 일부러 뺌
    assert tables.merge2("매일식품") == "매일식품"
    assert tables.merge2(None) is None


def test_쿠팡_보정은_묶음_키로(tables):
    assert tables.coupang_fix("623c387788362fa6") == "김종구부산어묵"   # brand.md 검증 예시
    assert tables.coupang_fix("0000000000000000") is None


# ── brand.md 「검증 예시」 15개 (마스터 실제 행, 갈래마다 하나) ──

EXAMPLES = [
    # 묶음 키, 제품명, 판매원, 제조사, 바코드 주인, 정답, 지나는 갈래
    ("01ea2c05fd91608c", "오빠닭 닭가슴살 비엔나 오리지널", None, "(주)오뗄 포천용정지점", None, "오빠닭", "사전"),
    ("0f1fabb7de5b1c44", "스팸 김밥햄", "씨제이제일제당(주)", "주식회사 하이푸드텍", None, "스팸", "하위 브랜드(SUB)"),
    ("fddacd062aba46be", "씨솔트 헤비브라운", "파파오가닉", "주식회사 케익모아", None, "파파오가닉", "판매원"),
    ("1a644d1a41493f2e", "쥬시쿨복숭아", None, "㈜빙그레", None, "빙그레", "브랜드(10-28 웹 판정)"),
    ("9adf34c22f57a6f1", "이천쌀호빵", None, "㈜에스피씨삼립", None, "삼립", "merge1 SPC삼립 → merge2 삼립"),
    ("7d177bfe40941408", "당근케이크", None, "(주)신세계푸드", "신세계푸드", "신세계푸드", "둘 다 · 자기 제품"),
    ("41df731033e76599", "월리 레드벨벳 치즈 케이크", None, "주식회사신세계푸드천안공장", "이마트", None, "둘 다 · 자기 제품 아님"),
    ("4465e400304351e7", "혜자)제육김치볶음밥", None, "(주)후레쉬퍼스트", None, None, "제조 전문"),
    ("ed520e25cccf2601", "오복순진간장", None, "(주)오복식품", None, None, "정한 곳이 규칙"),
    ("c3d2ad4688f573f7", "겉절이", None, "(주)이씨네식품", None, None, "이름표에 없음(신호 없음)"),
    ("7f012b25e4333f1b", "아몬드크로와상", None, "페이브베이커리", "다른회사식품", None, "바코드 주인이 다른 회사"),
    ("057d4cae8c6ef842", "켈슨 데니쉬버터쿠키 454G", None, "KELSENGROUPA/S", None, None, "clean(해외 꼬리)"),
    ("623c387788362fa6", "명품완도김어묵(냉동)", None, "주식회사케이제이지푸드", None, "김종구부산어묵", "쿠팡 보정"),
    ("b1b59fb6de275ae3", "빼빼로더블딥딸기화이트", None, "롯데제과주식회사", None, "롯데", "11-12 수동 고침"),
    ("4f80879f80305f34", "돼지고기호박짜글이 세트(W)", None, "(주)현대그린푸드 스마트푸드센터", None, None, "11-12 수동 비움"),
]


@pytest.mark.parametrize("g, name, seller, maker, owner, expected, branch", EXAMPLES, ids=[e[6] for e in EXAMPLES])
def test_brand_md_검증_예시(resolver, g, name, seller, maker, owner, expected, branch):
    assert resolver.resolve(BrandInput(g, name, seller, maker, None, owner)) == expected


def _input(name, seller, maker, owner=None):
    return BrandInput("0000000000000000", name, seller, maker, None, owner)


def test_메뉴_머리말이면_사전을_건너뛴다(resolver):
    # 「오빠닭」은 사전에 있지만 「오빠닭_ …」은 메뉴 머리말 — 사전을 건너뛰고 회사 이름으로 간다 (여기선 표에 없어 None)
    assert resolver.resolve(_input("오빠닭 세트", None, "표에없는회사")) == "오빠닭"
    assert resolver.resolve(_input("오빠닭_ 세트", None, "표에없는회사")) is None


def test_하위_브랜드는_그_회사_제품일_때만(resolver):
    assert resolver.resolve(_input("맛밤 군밤", "씨제이제일제당(주)", None)) == "맛밤"
    assert resolver.resolve(_input("맥심 모카골드", None, "동서식품(주)")) == "맥심"
    assert resolver.resolve(_input("맛밤 군밤", None, "(주)이씨네식품")) is None  # 다른 회사면 하위 브랜드 아님


def test_회사_이름이_하나도_없으면_None(resolver):
    assert resolver.resolve(_input("이름만있는제품", None, None)) is None


def test_바코드_주인이_계열사면_남의_제품으로_보지_않는다(resolver):
    assert resolver.resolve(_input("부라보콘", None, "해태아이스크림(주)", "빙그레")) == "해태아이스크림"
    assert resolver.resolve(_input("부라보콘", None, "해태아이스크림(주)", "다른회사식품")) is None


def test_조회표가_준비되지_않았으면_계산하지_않는다(tmp_path):
    not_ready = BrandResolver(BrandTables.load(tmp_path / "없는폴더"))
    assert not not_ready.ready
    with pytest.raises(TablesNotReady):
        not_ready.resolve(_input("오빠닭", None, None))


def test_지명_목록을_더하면_그_지명도_뗀다():
    r = rules_with_places(["천안", " 원주 ", "중", None])
    assert "천안" in r.place_names and "원주" in r.place_names and "중" not in r.place_names
    assert r.clean("주식회사신세계푸드천안공장") == "신세계푸드"
