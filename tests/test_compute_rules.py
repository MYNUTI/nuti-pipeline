"""ComputeRulesTest 이식 + 숫자 읽기."""
from __future__ import annotations

from nuti_pipeline.compute.rules import (Basis, amount, amount_of, basis, foodqr_nutrient_index, format_number, gtin14,
                                         number, per100, place_name, report_no, whole)


def test_보고번호는_첫_번째_숫자_11_16자리만():
    assert report_no("20020614179957") == "20020614179957"
    assert report_no(" 2002-0614 179957 / 19870190051644") == "20020614179957"
    assert report_no("1234567890") is None            # 10자리
    assert report_no("12345678901234567") is None     # 17자리
    assert report_no("IMP-2024-0001") is None         # 수입식품 내부 코드
    assert report_no("") is None


def test_바코드는_체크숫자_맞는_GTIN만_14자리로():
    assert gtin14("8809665551047") == "08809665551047"
    assert gtin14("08801007965697") == "08801007965697"
    assert gtin14("8809665551048") is None            # 체크숫자 틀림
    assert gtin14("880966555104") is None             # 체크 틀림
    assert gtin14("abc") is None


def test_매장코드와_묶음_인디케이터는_뺀다():
    assert gtin14("2000000000008") is None            # EAN-13 20~29
    assert gtin14("200000000004") is None             # UPC-12 2
    assert gtin14("400000000008") is None             # UPC-12 4
    assert gtin14("18809665551044") is None           # 묶음 인디케이터 1


def test_바코드_자릿수별_정규화():
    assert gtin14("036000291452") == "00036000291452"  # UPC-A
    assert gtin14("96385074") == "00000096385074"      # EAN-8
    assert gtin14("  8809665551047 ") == "08809665551047"


def test_공백이_섞인_바코드는_Java와_같게_버린다():
    # HACCP 는 "8 809678 470007" 처럼 공백을 넣어 준다. Java BarcodeNormalizer 는 숫자만 받으므로 None
    assert gtin14("8 809678 470007") is None


def test_총내용량은_g_mL로_맞춘다():
    assert amount("80.00g").format() == "80g"
    assert amount("2.45kg").format() == "2450g"
    assert amount("900mL").format() == "900mL"
    assert amount("1.8 L").format() == "1800mL"
    assert amount("9.5 g").format() == "9.5g"
    assert amount("1,200.000g").format() == "1200g"     # 영양성분DB Z10500 의 천 단위 쉼표
    assert amount_of("300", "g").format() == "300g"


def test_묶음_표기나_다른_모양은_버린다():
    assert amount("20g×10") is None
    assert amount("20g x 10") is None
    assert amount("1봉") is None
    assert amount("0g") is None
    assert amount(None) is None


def test_숫자는_천_단위_쉼표를_떼고_읽는다():
    assert number("1,670.000") == 1670.0
    assert number(" 137.000 ") == 137.0
    assert number("") is None
    assert number("-") is None
    assert number("1_000") is None     # Python float 은 받지만 Java 는 거부
    assert number("1.5f") == 1.5       # Java 는 받음


def test_푸드QR_영양_항목_이름():
    assert foodqr_nutrient_index("열량") == 0
    assert foodqr_nutrient_index("나트륨(mg)") == 1
    assert foodqr_nutrient_index("당류") == 3
    assert foodqr_nutrient_index("지방") == 4
    assert foodqr_nutrient_index("트랜스 지방") == 5
    assert foodqr_nutrient_index("포화지방") == 6
    assert foodqr_nutrient_index("단백질") == 9
    assert foodqr_nutrient_index("칼슘") == -1


def test_표시_기준에_따라_100g당으로():
    # 사천짜볶이: 총 내용량(300g)당 600kcal → 100g당 200, 한 봉지 600
    total = basis("총 내용량당", 300.0)
    assert total is Basis.TOTAL
    assert format_number(per100(600, total, 300.0)) == "200"
    assert format_number(per100(1340, total, 300.0)) == "446.67"
    assert format_number(whole(per100(1340, total, 300.0), amount("300g"))) == "1340"

    # 맥심 커피믹스: 단위 내용량(12g)당 50kcal → 100g당 416.67, 1180g 이면 4916.67
    unit = basis("단위 내용량당", 12.0)
    coffee = per100(50, unit, 12.0)
    assert format_number(coffee) == "416.67"
    assert format_number(whole(coffee, amount("1180g"))) == "4916.67"

    assert basis("100g당", 100.0) is Basis.PER_100
    assert basis("01", 100.0) is Basis.PER_100   # 글자로 모르면 기준량 100 → 100g당
    assert basis("02", 420.0) is Basis.TOTAL


def test_지명은_시군구를_뗀다():
    assert place_name("포천시") == "포천"
    assert place_name("서천군") == "서천"
    assert place_name("강남구") == "강남"
    assert place_name("중구") is None             # 떼면 1글자
    assert place_name("123") is None