"""BrandGroupingTest 이식."""
from __future__ import annotations

from nuti_pipeline.compute.grouping import Member, Stored, assign


def test_이미_정한_묶음은_바꾸지_않는다():
    r = assign([Member("D1", "새 이름", "회사", "100g")], {"D1": Stored("aaaaaaaaaaaaaaaa", True)})
    assert r.group_of["D1"] == "aaaaaaaaaaaaaaaa"
    assert r.assigned == []


def test_새_행은_세_값이_같은_기존_묶음에_들어간다():
    r = assign([Member("D1", "초코과자", "(주)회사", "100g"),
                Member("D9", " 초코과자 ", "(주)회사", "100g")],     # 앞뒤 공백만 다름
               {"D1": Stored("aaaaaaaaaaaaaaaa", True)})
    assert r.group_of["D9"] == "aaaaaaaaaaaaaaaa"
    assert len(r.assigned) == 1
    assert not r.assigned[0].representative
    assert r.representative_of["aaaaaaaaaaaaaaaa"] == "D1"


def test_없으면_새_묶음_대표는_가장_작은_food_cd():
    r = assign([Member("D5", "우유", "회사", "200mL"),
                Member("D3", "우유", "회사", "200mL"),
                Member("D4", "우유", "회사", "1000mL")], {})
    g = r.group_of["D3"]
    assert len(g) == 16
    assert r.group_of["D5"] == g
    assert r.group_of["D4"] != g          # 용량이 다르면 다른 묶음
    assert r.representative_of[g] == "D3"
    assert len(r.assigned) == 3


def test_같은_입력이면_같은_키():
    a = assign([Member("D1", "우유", "회사", "200mL")], {})
    b = assign([Member("D7", "우유", "회사", "200mL")], {})
    assert a.group_of["D1"] == b.group_of["D7"]


def test_저장된_대표가_사라지면_가장_작은_food_cd():
    r = assign([Member("D8", "우유", "회사", "200mL"), Member("D6", "우유", "회사", "200mL")],
               {"D8": Stored("bbbbbbbbbbbbbbbb", False), "D6": Stored("bbbbbbbbbbbbbbbb", False)})
    assert r.representative_of["bbbbbbbbbbbbbbbb"] == "D6"
