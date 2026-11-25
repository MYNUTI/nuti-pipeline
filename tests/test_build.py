"""build 전체 흐름 — 원본을 state.db 에 직접 넣고 seed → build → 분류 검토 → 다시 build 까지."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from nuti_pipeline import build
from nuti_pipeline.apis import REQUIRED_FOR_BUILD
from nuti_pipeline.brand.tables import BrandTables
from nuti_pipeline.csv_out import read_master
from nuti_pipeline.state import db


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def tables(tmp_path) -> BrandTables:
    """테스트용 작은 조회표 — 사전에 「바삭」 하나."""
    d = tmp_path / "brand"
    d.mkdir()
    _write(d / "1_브랜드사전_2026-01-01.csv", ["낱말(정규화)", "브랜드(표시 이름)", "붙여 쓴 이름에도 씀"], [["바삭", "바삭", "N"]])
    _write(d / "2_회사이름표_2026-01-01.csv", ["이름", "이름표", "정한 곳"], [["회사", "제조 전문", "AI 판정"]])
    _write(d / "3_같은회사_합치기표_2026-01-01.csv", ["원래 이름", "보일 이름"], [])
    _write(d / "4_쿠팡보정_고정값_2026-01-01.csv", ["영양 묶음 키", "브랜드(지금 값)"], [])
    t = BrandTables.load(d)
    assert t.ready, t.problem
    return t


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "state.db")
    yield c
    c.close()


def add_round(conn, api: str, rows: list[dict], *, status="DONE", mode="FULL", finished="2026-11-23T05:00:00") -> int:
    with conn:
        rid = conn.execute("INSERT INTO rounds(api, mode, status, started_at, finished_at) VALUES (?,?,?,?,?)",
                           (api, mode, status, "2026-11-22T20:00:00", finished if status != "RUNNING" else None)).lastrowid
        conn.executemany("INSERT INTO raw(api, round_id, row_hash, data) VALUES (?,?,?,?)",
                         [(api, rid, str(i), json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows)])
    return rid


def nutri(fid, name, report=None, **extra):
    m = {"FOOD_CD": fid, "FOOD_NM_KR": name, "DB_GRP_NM": "가공식품", "DB_CLASS_NM": "상용제품", "MAKER_NM": "회사",
         "Z10500": "100g", "AMT_NUM1": "100", "FOOD_REF_NM": "과자"}
    if report:
        m["ITEM_REPORT_NO"] = report
    m.update(extra)
    return m


def fill_all(conn, nutri_rows, **by_api):
    for api in REQUIRED_FOR_BUILD:
        rows = nutri_rows if api == "NUTRI_DB" else by_api.get(api, [{"_": "x"}])
        add_round(conn, api, rows)


def seed(conn, tmp_path, categories: dict[str, str], groups: list[tuple[str, str, str]]):
    master = tmp_path / "product_master.csv"
    _write(master, ["master_id", "category"], [[k, v] for k, v in categories.items()])
    _write(tmp_path / "groups.csv", ["food_cd", "group_key", "is_representative"], [list(g) for g in groups])
    build.seed_categories(conn, master)
    build.seed_groups(conn, tmp_path / "groups.csv")


def test_전량을_끝까지_받지_않은_API가_있으면_만들지_않는다(conn, tmp_path, tables):
    add_round(conn, "NUTRI_DB", [nutri("D1", "과자")])
    rep = build.run(conn, tmp_path / "csv", tables=tables)
    assert rep.file is None
    assert any("C002" in s for s in rep.skipped)


def test_seed_없이는_거부(conn, tmp_path, tables):
    fill_all(conn, [nutri("D1", "과자")])
    with pytest.raises(build.BuildRefused, match="분류표"):
        build.run(conn, tmp_path / "csv", tables=tables)


def test_build_전체_흐름(conn, tmp_path, tables):
    fill_all(conn, [nutri("D1", "바삭 과자", "20020614179957"), nutri("D2", "새 과자")],
             C002=[{"PRDLST_REPORT_NO": "20020614179957", "RAWMTRL_NM": "밀가루", "CHNG_DT": "20261101"},
                   {"PRDLST_REPORT_NO": "99999999999", "RAWMTRL_NM": "남의 제품", "CHNG_DT": "20261101"}],
             I2852=[{"PRDLST_REPORT_NO": "20020614179957"}])
    seed(conn, tmp_path, {"D1": "0101"}, [("D1", "aaaaaaaaaaaaaaaa", "true")])
    csv_dir = tmp_path / "csv"

    rep = build.run(conn, csv_dir, tables=tables)
    assert rep.file is not None and rep.file.parent == csv_dir
    rows = {r["master_id"]: r for r in read_master(rep.file)}
    assert set(rows) == {"D1"}                         # D2 는 분류가 없어 이번 CSV 에서 빠짐
    assert rows["D1"]["category"] == "0101"
    assert rows["D1"]["brand"] == "바삭"
    assert rows["D1"]["ingredients"] == "밀가루"
    assert rows["D1"]["is_discontinued"] == "true"
    assert rep.held_back == ["D2"]
    assert conn.execute("SELECT group_key FROM brand_groups WHERE food_cd='D1'").fetchone()[0] == "aaaaaaaaaaaaaaaa"
    assert conn.execute("SELECT COUNT(*) FROM brand_groups").fetchone()[0] == 2   # D2 의 새 묶음 저장

    # 새로 끝난 회차가 없으면 만들지 않는다
    assert build.run(conn, csv_dir, tables=tables).skipped

    # 분류 검토 → 넣기 → 다시 만들면 D2 도 들어간다
    review = tmp_path / "review.csv"
    assert build.export_review(conn, review) == 1
    with open(review, encoding="utf-8-sig", newline="") as f:
        lines = list(csv.DictReader(f))
    assert lines[0]["master_id"] == "D2" and lines[0]["food_type"] == "과자"
    lines[0]["category"] = "0202"
    _write(review, list(lines[0]), [list(lines[0].values())])
    assert build.import_review(conn, review) == (1, 0)
    assert conn.execute("SELECT COUNT(*) FROM category_review").fetchone()[0] == 0

    rep2 = build.run(conn, csv_dir, tables=tables, force=True, again=True)
    assert rep2.file.name.endswith("_2.csv")
    rows2 = {r["master_id"]: r for r in read_master(rep2.file)}
    assert rows2["D2"]["category"] == "0202"

    d = build.diff(rep.file, rep2.file)
    assert d.added == ["D2"] and d.same == 1 and d.changed == 0


def test_묶음_구성_없이도_만들고_키를_저장한다(conn, tmp_path, tables):
    fill_all(conn, [nutri("D1", "과자"), nutri("D2", "과자")])
    master = tmp_path / "product_master.csv"
    _write(master, ["master_id", "category"], [["D1", "0101"], ["D2", "0101"]])
    build.seed_categories(conn, master)
    rep = build.run(conn, tmp_path / "csv", tables=tables)
    assert rep.rows == 2
    keys = {r[0] for r in conn.execute("SELECT DISTINCT group_key FROM brand_groups")}
    assert len(keys) == 1                    # 같은 제품명 · 제조사 · 용량 → 한 묶음
    assert rep.coupang_total == 0 and rep.coupang_matched == 0


def test_받는_중인_회차는_계산에_안_보인다(conn, tmp_path, tables):
    fill_all(conn, [nutri("D1", "옛 이름")])
    add_round(conn, "NUTRI_DB", [nutri("D1", "받는 중 이름")], status="RUNNING")
    seed(conn, tmp_path, {"D1": "0101"}, [("D1", "aaaaaaaaaaaaaaaa", "true")])
    rep = build.run(conn, tmp_path / "csv", tables=tables)
    assert next(iter(read_master(rep.file)))["product_name"] == "옛 이름"


def test_dry_run은_preview에만_쓰고_상태를_안_바꾼다(conn, tmp_path, tables):
    fill_all(conn, [nutri("D1", "과자"), nutri("D2", "새 과자")])
    seed(conn, tmp_path, {"D1": "0101"}, [("D1", "aaaaaaaaaaaaaaaa", "true")])
    rep = build.run(conn, tmp_path / "csv", tables=tables, dry_run=True)
    assert rep.file.parent.name == "preview"
    assert not (tmp_path / "csv").exists()
    assert conn.execute("SELECT COUNT(*) FROM builds").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM brand_groups").fetchone()[0] == 1


def test_seed는_이미_있으면_replace_없이_거부(conn, tmp_path):
    seed(conn, tmp_path, {"D1": ""}, [("D1", "aaaaaaaaaaaaaaaa", "1")])
    assert conn.execute("SELECT category FROM category_map WHERE master_id='D1'").fetchone()[0] is None
    with pytest.raises(build.BuildRefused):
        build.seed_categories(conn, tmp_path / "product_master.csv")
    assert build.seed_categories(conn, tmp_path / "product_master.csv", replace=True) == (1, 0)