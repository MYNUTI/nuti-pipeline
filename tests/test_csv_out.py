from __future__ import annotations

from datetime import date

import pytest

from nuti_pipeline import content_hash as ch
from nuti_pipeline.columns import MASTER_COLUMNS
from nuti_pipeline.csv_out import read_master, write_master


def _rows():
    return [
        {"master_id": "D1", "source": "nutri_db", "product_name": '쉼표, "따옴표"', "content_hash": "거짓값"},
        {"master_id": "FQR-2", "source": "foodqr", "product_name": "줄\n바꿈", "is_discontinued": "true"},
    ]


def test_헤더_34칸_hash는_다시_계산(tmp_path):
    path, n = write_master(_rows(), tmp_path, day=date(2026, 11, 23))
    assert path.name == "product_master_20261123.csv"
    assert n == 2
    assert not list(tmp_path.glob("*.tmp"))
    with open(path, encoding="utf-8") as f:
        assert f.readline().rstrip("\n").split(",") == list(MASTER_COLUMNS)
    got = list(read_master(path))
    assert got[0]["product_name"] == '쉼표, "따옴표"'
    assert got[1]["product_name"] == "줄\n바꿈"
    for r in got:
        assert r["content_hash"] == ch.compute(r)


def test_같은_날_두_번째는_거부_또는_2(tmp_path):
    d = date(2026, 11, 23)
    write_master(_rows(), tmp_path, day=d)
    with pytest.raises(FileExistsError):
        write_master(_rows(), tmp_path, day=d)
    p, _ = write_master(_rows(), tmp_path, day=d, allow_second=True)
    assert p.name == "product_master_20261123_2.csv"


def test_실패하면_tmp도_남지_않는다(tmp_path):
    rows = _rows() + [{"master_id": "D1"}]  # 중복
    with pytest.raises(ValueError):
        write_master(rows, tmp_path, day=date(2026, 11, 23))
    assert list(tmp_path.iterdir()) == []
