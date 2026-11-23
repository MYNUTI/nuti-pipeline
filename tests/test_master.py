"""MasterBuilderTest 이식."""
from __future__ import annotations

import pytest

from nuti_pipeline import content_hash
from nuti_pipeline.compute.master import BrandInput, MasterBuilder, MasterInput


@pytest.fixture
def brand_inputs() -> list[BrandInput]:
    return []


@pytest.fixture
def builder(brand_inputs):
    def brand(i: BrandInput) -> str:
        brand_inputs.append(i)
        return f"브랜드-{i.product_name}"
    return MasterBuilder(brand_fn=brand)


def nutri(fid, name, maker, report, weight, kcal, sodium, **extra):
    m = {"FOOD_CD": fid, "FOOD_NM_KR": name, "DB_GRP_NM": "가공식품", "DB_CLASS_NM": "상용제품",
         "MAKER_NM": maker, "Z10500": weight, "AMT_NUM1": kcal, "AMT_NUM13": sodium}
    if report is not None:
        m["ITEM_REPORT_NO"] = report
    m.update(extra)
    return m


def foodqr(elb, barcode, name, ctv, ymd, ver, **extra):
    m = {"ELB_MNG_NO": elb, "LGG_CD": "001", "PRDCT_NM": name, "CTV": ctv, "VLD_BGNG_YMD": ymd,
         "VER_NO": ver, "BZENTY_NM": "업체"}
    if barcode is not None:
        m["BRCD_NO"] = barcode
    m.update(extra)
    return m


def nutrition(elb, report, sn, cd, tct, tcd, name, cta):
    return {"ELB_MNG_NO": elb, "IMRPT_NO": report, "NTRTN_INDCT_SN": sn, "NTRTN_INDCT_CD": cd,
            "NTRTN_INDCT_TCT": tct, "NTRTN_INDCT_TCD": tcd, "NIRWMT_NM": name, "CTA": cta}


def row(r, mid):
    return next(m for m in r.rows if m["master_id"] == mid)


# ── 영양성분DB ──

def test_영양성분DB_행은_가공식품_상용제품만(builder):
    r = builder.build(MasterInput(nutri_db=[
        nutri("D1", "과자", "(주)회사", "20020614179957", "300g", "200", "446.666666"),
        nutri("D2", "품목", "(주)회사", None, "100g", "1", "1", DB_CLASS_NM="품목대표"),
    ]))
    assert len(r.rows) == 1
    d1 = row(r, "D1")
    assert d1["source"] == "nutri_db"
    assert d1["report_no"] == "20020614179957"
    assert d1["product_name"] == "과자"
    assert d1["maker"] == "(주)회사"
    assert d1["serving_size"] == "300g"
    assert d1["calories_kcal_per_100g"] == "200"
    assert d1["calories_kcal"] == "600"
    assert d1["sodium_mg_per_100g"] == "446.67"
    assert d1["sodium_mg"] == "1340"
    assert d1["sugar_g"] is None             # 원천에 없으면 빈 값
    assert d1["is_discontinued"] == "false"
    assert d1["content_hash"] == content_hash.compute(d1)


def test_같은_food_cd가_여러_번이면_나중_것(builder):
    r = builder.build(MasterInput(nutri_db=[
        nutri("D1", "옛 이름", "회사", None, "100g", "1", "1"),
        nutri("D1", "새 이름", "회사", None, "100g", "1", "1"),
    ]))
    assert len(r.rows) == 1
    assert row(r, "D1")["product_name"] == "새 이름"


def test_영양성분DB_숫자의_천_단위_쉼표(builder):
    r = builder.build(MasterInput(nutri_db=[nutri("D1", "과자", "회사", None, "1kg", "1,670.000", "1")]))
    assert row(r, "D1")["calories_kcal_per_100g"] == "1670"
    assert row(r, "D1")["calories_kcal"] == "16700"


# ── barcode ──

def test_바코드는_보고번호로_우선순위_순서대로(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "과자", "회사", "20020614179957", "100g", "1", "1")],
        c005=[{"PRDLST_REPORT_NO": "20020614179957", "BAR_CD": "8801234567893"}],
        i0490=[{"PRDLST_REPORT_NO": "20020614179957", "BRCDNO": "8809665551047"}],
    ))
    assert row(r, "D1")["barcode"] == "08809665551047"  # I0490 > C005


def test_보고번호로_못_찾으면_제품명과_제조사로(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "초코 과자", "(주)회사", None, "100g", "1", "1")],
        c005=[{"PRDLST_REPORT_NO": "11111111111", "BAR_CD": "8801234567893", "PRDLST_NM": "초코과자",
               "BSSH_NM": "주식회사 회사"}],
    ))
    assert row(r, "D1")["barcode"] == "08801234567893"
    assert r.stats.barcode_by_name == 1


def test_한_바코드에_생산_중_2개면_둘_다_비운다(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "가", "회사", "20020614179957", "100g", "1", "1"),
                  nutri("D2", "나", "회사", "20020614179958", "100g", "1", "1")],
        c005=[{"PRDLST_REPORT_NO": "20020614179957", "BAR_CD": "8801234567893"},
              {"PRDLST_REPORT_NO": "20020614179958", "BAR_CD": "8801234567893"}],
    ))
    assert row(r, "D1")["barcode"] is None
    assert row(r, "D2")["barcode"] is None
    assert r.stats.barcode_cleared == 2


def test_생산_중_1개와_생산중단이면_생산_중_쪽만_남긴다(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "가", "회사", "20020614179957", "100g", "1", "1"),
                  nutri("D2", "나", "회사", "20020614179958", "100g", "1", "1")],
        c005=[{"PRDLST_REPORT_NO": "20020614179957", "BAR_CD": "8801234567893"},
              {"PRDLST_REPORT_NO": "20020614179958", "BAR_CD": "8801234567893"}],
        discontinued=frozenset({"20020614179958"}),
    ))
    assert row(r, "D1")["barcode"] == "08801234567893"
    assert row(r, "D2")["barcode"] is None
    assert row(r, "D2")["is_discontinued"] == "true"


# ── 원재료 ──

def test_원재료는_C002_C006_HACCP_순서_같은_출처는_CHNG_DT_최신(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "가", "회사", "20020614179957", "100g", "1", "1"),
                  nutri("D2", "나", "회사", "20020614179958", "100g", "1", "1"),
                  nutri("D3", "다", "회사", "20020614179959", "100g", "1", "1")],
        c002=[{"PRDLST_REPORT_NO": "20020614179957", "RAWMTRL_NM": "새 원재료", "CHNG_DT": "20260101"},
              {"PRDLST_REPORT_NO": "20020614179957", "RAWMTRL_NM": "옛 원재료", "CHNG_DT": "20250101"}],
        c006=[{"PRDLST_REPORT_NO": "20020614179957", "RAWMTRL_NM": "축산", "CHNG_DT": "20270101"},
              {"PRDLST_REPORT_NO": "20020614179958", "RAWMTRL_NM": "축산 원재료", "CHNG_DT": "20250101"}],
        haccp=[{"prdlstReportNo": "20020614179958", "rawmtrl": "HACCP 원재료"},
               {"prdlstReportNo": "20020614179959", "rawmtrl": "알수없음"}],
    ))
    assert row(r, "D1")["ingredients"] == "새 원재료"
    assert row(r, "D2")["ingredients"] == "축산 원재료"
    assert row(r, "D3")["ingredients"] is None


# ── 푸드QR ──

def test_푸드QR은_한국어_바코드당_최신_판_영양을_100g당으로(builder):
    r = builder.build(MasterInput(
        foodqr_list=[
            foodqr("1000", "8801007965697", "해물죽", "420g", "20240101", "1"),
            foodqr("1001", "8801007965697", "해물죽", "420g", "20250101", "1"),
            foodqr("1003", None, "Porridge", "420g", "20250101", "1", LGG_CD="002"),
        ],
        foodqr_nutrition=[
            nutrition("1001", "19910144038912", "1", "총 내용량당", "420", "g", "열량", "245"),
            nutrition("1001", "19910144038912", "1", "총 내용량당", "420", "g", "나트륨", "780"),
            nutrition("1001", "19910144038912", "2", "100g당", "100", "g", "열량", "9999"),
        ],
    ))
    assert len(r.rows) == 1
    q = row(r, "FQR-1001")
    assert q["source"] == "foodqr"
    assert q["barcode"] == "08801007965697"
    assert q["report_no"] == "19910144038912"
    assert q["maker"] == "업체"                     # 제조 표가 없으면 목록 BZENTY_NM
    assert q["calories_kcal_per_100g"] == "58.33"
    assert q["calories_kcal"] == "245"
    assert q["sodium_mg_per_100g"] == "185.71"
    assert q["sodium_mg"] == "780"
    assert q["brand"] is None                       # 푸드QR 은 브랜드 규칙 밖


def test_영양성분DB와_보고번호가_겹치는_푸드QR은_뺀다(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "과자", "회사", "20020614179957", "100g", "1", "1")],
        foodqr_list=[foodqr("2000", None, "과자", "100g", "20250101", "1")],
        foodqr_maker=[{"ELB_MNG_NO": "2000", "IMRPT_NO": "20020614179957", "BUES_NM": "제조사"}],
    ))
    assert len(r.rows) == 1
    assert r.stats.foodqr_excluded == 1


# ── brand · category ──

def test_브랜드는_묶음_대표_행으로_묶음_전체에_같은_값(builder, brand_inputs):
    r = builder.build(MasterInput(nutri_db=[
        nutri("D2", "우유", "회사", None, "200mL", "1", "1"),
        nutri("D1", "우유", "회사", None, "200mL", "1", "1"),
    ]))
    assert row(r, "D1")["brand"] == "브랜드-우유"
    assert row(r, "D2")["brand"] == "브랜드-우유"
    assert len(brand_inputs) == 1
    assert len(r.grouping.assigned) == 2
    assert r.stats.new_groups == 1


def test_바코드_주인은_판매원이_붙은_880_번호로_정한다(builder, brand_inputs):
    builder.build(MasterInput(
        nutri_db=[nutri("D1", "가", "제조사A", "20020614179957", "100g", "1", "1", SELLER_MANUFAC_NM="(주)판매회사"),
                  nutri("D2", "나", "제조사B", "20020614179958", "100g", "1", "1")],
        c005=[{"PRDLST_REPORT_NO": "20020614179957", "BAR_CD": "8801234567893"},
              {"PRDLST_REPORT_NO": "20020614179958", "BAR_CD": "8801234560009"}],
    ))
    d2 = next(i for i in brand_inputs if i.product_name == "나")
    assert d2.barcode_owner == "판매회사"   # 앞 9자리 880123456 의 주인


def test_분류는_넘겨준_값을_쓰고_없는_새_제품은_검토_목록(builder):
    r = builder.build(MasterInput(
        nutri_db=[nutri("D1", "가", "회사", None, "100g", "1", "1"), nutri("D2", "나", "회사", None, "100g", "1", "1")],
        categories={"D1": "0505"},
    ))
    assert row(r, "D1")["category"] == "0505"
    assert row(r, "D2")["category"] is None
    assert r.new_without_category == ["D2"]
    assert r.stats.new_products == 1
