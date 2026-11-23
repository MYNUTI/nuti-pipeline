"""ContentHashCalculatorTest 이식. product_master.csv 전체 대조는 PRODUCT_MASTER_CSV 가 있을 때만."""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from nuti_pipeline import content_hash as ch
from nuti_pipeline.columns import HASH_COLUMNS, MASTER_COLUMNS, NUMERIC_COLUMNS
from nuti_pipeline.csv_out import read_master


def _row() -> dict[str, str]:
    row = {c: "" for c in MASTER_COLUMNS}
    row.update(master_id="D000001", source="nutri_db", product_name="테스트", sodium_mg="1340",
               calories_kcal="395.2", is_discontinued="false")
    return row


def test_숫자는_소수_둘째_자리로_맞추고_끝의_0과_점을_뗀다():
    assert ch.format_number(395.20) == "395.2"
    assert ch.format_number(80.00) == "80"
    assert ch.format_number(600.0) == "600"
    assert ch.format_number(446.666666) == "446.67"
    assert ch.format_number(726126.3) == "726126.3"
    assert ch.format_number(0.0) == "0"
    assert ch.format_number(1e16) == "10000000000000000"  # 지수 표기 없음


def test_음수_0은_0():
    assert ch.format_number(-0.0) == "0"
    assert ch.format_number(-0.001) == "0"


def test_경계값은_Python_round와_같다():
    assert ch.format_number(2.675) == "2.67"
    assert ch.format_number(0.125) == "0.12"
    assert ch.format_number(0.375) == "0.38"
    for x in (2.675, 0.125, 0.375, 1.005, 12.345, 99.995, 0.005, 0.015):
        assert ch.format_number(x) == ch.format_number(round(x, 2))


def test_Decimal_입력도_float_2진_값_기준():
    assert ch.format_number(Decimal("2.675")) == "2.67"
    assert ch.format_number(None) == ""


def test_nan은_거부():
    with pytest.raises(ValueError):
        ch.format_number(float("nan"))


def test_참거짓():
    assert ch.format_bool(True) == "true"
    assert ch.format_bool(False) == "false"
    assert ch.format_bool(None) == ""


def test_대상은_30칸이고_순서가_고정이다():
    assert len(HASH_COLUMNS) == 30
    assert HASH_COLUMNS[0] == "barcode"
    assert HASH_COLUMNS[8] == "serving_size"
    assert HASH_COLUMNS[9] == "calories_kcal"
    assert HASH_COLUMNS[19] == "calories_kcal_per_100g"
    assert HASH_COLUMNS[29] == "is_discontinued"
    assert not {"master_id", "content_hash", "created_at", "updated_at"} & set(HASH_COLUMNS)


def test_대상_밖_컬럼은_영향이_없다():
    row = _row()
    before = ch.compute(row)
    row["master_id"] = "다른값"
    row["updated_at"] = "2099-01-01T00:00:00"
    assert ch.compute(row) == before


def test_대상_칸이_바뀌면_달라진다():
    row = _row()
    before = ch.compute(row)
    row["sodium_mg"] = "1341"
    assert ch.compute(row) != before


def test_없는_칸과_None은_빈_문자열():
    empty, none, missing = _row(), _row(), _row()
    empty["brand"] = ""
    none["brand"] = None
    del missing["brand"]
    assert ch.compute(none) == ch.compute(empty) == ch.compute(missing)


_MASTER = os.getenv("PRODUCT_MASTER_CSV")


@pytest.mark.skipif(not _MASTER or not Path(_MASTER).exists(), reason="PRODUCT_MASTER_CSV 없음")
def test_product_master_전체가_파일의_hash와_같다():
    bad = []
    n = 0
    for row in read_master(Path(_MASTER)):
        n += 1
        # 숫자로 바꿨다가 다시 맞춰도 같아야 함 (계산 경로 흉내)
        typed = dict(row)
        for c in NUMERIC_COLUMNS:
            v = row.get(c, "")
            typed[c] = ch.format_number(float(v) if v else None)
        if ch.compute(row) != row["content_hash"] or ch.compute(typed) != row["content_hash"]:
            bad.append(row["master_id"])
    assert n > 0
    assert bad == [], f"{len(bad)}행 불일치, 예: {bad[:5]}"
