"""build — state.db 의 끝까지 받은(DONE) 원본으로 34컬럼을 계산해 CSV 를 만든다 (Java ProductRecomputeService 자리).

순서
  1. 완료 확인: 계산에 필요한 API 11개가 모두 전량을 한 번 이상 끝까지 받았는가
     (Java 와 달리 마지막 회차가 받는 중이어도 막지 않는다 — DONE 회차의 행만 읽으므로 이전 스냅샷으로 계산된다)
  2. 새 자료 확인: 마지막 build 뒤에 새로 끝난 회차가 없으면 CSV 를 만들지 않는다 (약속의 「파일이 없는 날」)
  3. 원본 읽기: 큰 API(C002 · C006 · I2852)는 우리 제품 보고번호만 남긴다
  4. 계산: MasterBuilder + BrandResolver (지명 목록 = 시도 17개 + I0300 SITE_ADDR)
  5. 분류: 분류표(category_map)에 있는 제품은 그 값. 없는 새 제품은 규칙 → 그래도 없으면 이번 CSV 에서 빼고 검토 대기로
  6. 저장: 새 묶음 · 검토 대기 · build 이력, CSV (.tmp → .csv)

묶음 구성(데이터 팀의 food_cd → 묶음 키)은 받을 수 없어서, 처음 build 때 공식으로 묶음 키를 만들어 저장하고 이후 고정한다.
쿠팡 보정 · 11-12 고침 키가 몇 개 맞는지 결과에 출력한다.
"""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .apis import APIS, REQUIRED_FOR_BUILD
from .brand.resolver import BrandResolver, rules_with_places
from .brand.tables import BrandTables
from .collect.collector import TS, kst_now
from .compute.grouping import Stored
from .compute.master import FOODQR_PREFIX, MasterBuilder, MasterInput, Stats
from .compute.rules import place_name, report_no, text
from .csv_out import write_master

log = logging.getLogger(__name__)

PLACE_APIS = ("I0300",)


class BuildRefused(RuntimeError):
    """계산하면 안 되는 상태 (조회표 · 분류표 · 묶음이 준비되지 않음 등)."""


@dataclass
class BuildReport:
    skipped: list[str] = field(default_factory=list)      # 만들지 않은 이유 (비면 만든 것)
    file: Path | None = None
    rows: int = 0
    held_back: list[str] = field(default_factory=list)    # 분류 대기로 뺀 새 제품
    new_groups: int = 0
    stats: Stats | None = None
    place_names: int = 0
    coupang_matched: int = 0    # 쿠팡 보정 키 중 이번 묶음 키와 맞은 수 (안 맞으면 그 보정은 적용되지 않음)
    coupang_total: int = 0
    fix_matched: int = 0        # 11-12 고침 키 중 맞은 수
    fix_total: int = 0


# ── 원본 읽기 ─────────────────────────────────────────────────────────────

def _s(v) -> str | None:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v, ensure_ascii=False)


def raw_rows(conn: sqlite3.Connection, api: str) -> Iterator[dict[str, str | None]]:
    """api 의 DONE 회차 행을 받은 순서대로. 값은 문자열 (없으면 None)."""
    cur = conn.execute(
        "SELECT r.data FROM raw r JOIN rounds o ON o.id = r.round_id "
        "WHERE r.api = ? AND o.status = 'DONE' ORDER BY r.id", (api,))
    for (data,) in cur:
        yield {k: _s(v) for k, v in json.loads(data).items()}


def blockers(conn: sqlite3.Connection) -> list[str]:
    """계산을 막는 이유. 필요한 API 가 전량을 한 번도 끝까지 받지 못했으면 막는다."""
    out = []
    for name in REQUIRED_FOR_BUILD:
        ok = conn.execute("SELECT 1 FROM rounds WHERE api=? AND mode='FULL' AND status='DONE' LIMIT 1", (name,)).fetchone()
        if not ok:
            out.append(f"{name}({APIS[name].label}): 전량을 끝까지 받은 적이 없습니다")
    return out


def seen_until(conn: sqlite3.Connection) -> str:
    """계산에 쓰는 API 의 DONE 회차 중 가장 늦은 finished_at."""
    names = (*REQUIRED_FOR_BUILD, *PLACE_APIS)
    row = conn.execute(f"SELECT MAX(finished_at) FROM rounds WHERE status='DONE' AND api IN ({','.join('?' * len(names))})",
                       names).fetchone()
    return row[0] or ""


def has_new_data(conn: sqlite3.Connection) -> bool:
    last = conn.execute("SELECT seen_until FROM builds WHERE file_name IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return last is None or seen_until(conn) > last[0]


def place_names(conn: sqlite3.Connection) -> set[str]:
    """I0300 SITE_ADDR 둘째 낱말에서 시 · 군 · 구를 뗀 2글자 이상 한글."""
    out: set[str] = set()
    for api in PLACE_APIS:
        for r in raw_rows(conn, api):
            addr = (r.get("SITE_ADDR") or "").strip(" ")
            parts = addr.split(" ")
            if len(parts) >= 2:
                p = place_name(parts[1])
                if p:
                    out.add(p)
    return out


def load_input(conn: sqlite3.Connection) -> tuple[MasterInput, dict[str, str | None]]:
    """원본 → MasterInput. 두 번째 값은 master_id → 원천 식품유형 (분류 검토용)."""
    nutri = list(raw_rows(conn, "NUTRI_DB"))
    foodqr_list = list(raw_rows(conn, "FOODQR_LIST"))
    nutrition = list(raw_rows(conn, "FOODQR_NUTRITION"))
    maker = list(raw_rows(conn, "FOODQR_MAKER"))

    # 큰 API 는 우리 제품 보고번호만 남긴다 (영양성분DB + 푸드QR 영양 · 제조의 보고번호)
    needed = {report_no(r.get("ITEM_REPORT_NO")) for r in nutri}
    needed |= {report_no(r.get("IMRPT_NO")) for r in (*nutrition, *maker)}
    needed.discard(None)

    def ours(api: str) -> list[dict[str, str | None]]:
        return [r for r in raw_rows(conn, api) if report_no(r.get("PRDLST_REPORT_NO")) in needed]

    discontinued = frozenset(rn for r in raw_rows(conn, "I2852")
                             if (rn := report_no(r.get("PRDLST_REPORT_NO"))) in needed)

    categories = {r["master_id"]: r["category"] for r in conn.execute("SELECT master_id, category FROM category_map")}
    stored = {r["food_cd"]: Stored(r["group_key"], bool(r["is_representative"]))
              for r in conn.execute("SELECT food_cd, group_key, is_representative FROM brand_groups")}

    food_type: dict[str, str | None] = {}
    for r in nutri:
        if (fid := text(r.get("FOOD_CD"))) is not None:
            food_type[fid] = text(r.get("FOOD_REF_NM"))
    for r in foodqr_list:
        if (elb := text(r.get("ELB_MNG_NO"))) is not None:
            food_type[FOODQR_PREFIX + elb] = text(r.get("FOOD_TYPE_CD_NM"))

    inp = MasterInput(
        nutri_db=nutri, foodqr_list=foodqr_list, foodqr_nutrition=nutrition, foodqr_maker=maker,
        haccp=list(raw_rows(conn, "HACCP")), c005=list(raw_rows(conn, "C005")), i2570=list(raw_rows(conn, "I2570")),
        c002=ours("C002"), c006=ours("C006"), i0490=list(raw_rows(conn, "I0490")),
        discontinued=discontinued, categories=categories, stored_groups=stored,
    )
    return inp, food_type


# ── 분류 ──────────────────────────────────────────────────────────────────

def categorize_new(rows: list[dict[str, str | None]], food_type: dict[str, str | None]) -> dict[str, str]:
    """분류표에 없는 새 제품에 분류 규칙을 적용한다 → master_id → 분류 코드.
    TODO: build.py 의 category_rules.csv · category_manual.csv 규칙을 옮겨 채운다. 지금은 규칙이 없어 모두 검토 대기로 간다."""
    return {}


# ── build ─────────────────────────────────────────────────────────────────

def run(conn: sqlite3.Connection, csv_dir: Path, *, force: bool = False, again: bool = False,
        dry_run: bool = False, tables: BrandTables | None = None) -> BuildReport:
    rep = BuildReport()

    if b := blockers(conn):
        rep.skipped = b
        return rep
    if not force and not has_new_data(conn):
        rep.skipped = ["마지막 build 뒤에 새로 끝난 회차가 없습니다"]
        return rep

    tables = tables or BrandTables.load()
    if not tables.ready:
        raise BuildRefused(f"브랜드 조회표가 준비되지 않았습니다: {tables.problem}")
    if conn.execute("SELECT COUNT(*) FROM category_map").fetchone()[0] == 0:
        raise BuildRefused("분류표가 비어 있습니다. 먼저 pipeline seed categories <product_master.csv>")
    # 묶음 구성은 없어도 된다 — 없으면 묶음 키를 공식(제품명 · 제조사 · 총내용량의 sha256 앞 16자리)으로 새로 만들고 저장한다.
    # 데이터 팀 키와 공식이 같으면 쿠팡 보정 · 11-12 고침이 그대로 걸린다. 몇 개가 맞았는지 결과에 남긴다

    until = seen_until(conn)
    inp, food_type = load_input(conn)
    places = place_names(conn)
    rep.place_names = len(places)
    rules = rules_with_places(places)
    result = MasterBuilder(rules, BrandResolver(tables, rules).resolve).build(inp)
    rep.stats = result.stats
    rep.new_groups = result.stats.new_groups
    keys = set(result.grouping.group_of.values())
    rep.coupang_total, rep.coupang_matched = len(tables.coupang_fix_map), len(keys & set(tables.coupang_fix_map))
    rep.fix_total, rep.fix_matched = len(tables.fix1112_map), len(keys & set(tables.fix1112_map))

    # 분류: 새 제품 → 규칙 → 남은 것은 이번 CSV 에서 뺀다
    new_ids = set(result.new_without_category)
    by_rule = categorize_new([r for r in result.rows if r["master_id"] in new_ids], food_type)
    held = [m for m in result.new_without_category if m not in by_rule]
    held_set = set(held)
    rep.held_back = held
    now = kst_now().strftime(TS)
    out_rows = []
    for r in result.rows:
        if r["master_id"] in held_set:
            continue
        if r["master_id"] in by_rule:
            r["category"] = by_rule[r["master_id"]]
        out_rows.append(r)

    if dry_run:
        preview_dir = csv_dir.parent / "preview"
        path, n = write_master(out_rows, preview_dir, allow_second=True)
        rep.file, rep.rows = path, n
        return rep

    with conn:
        conn.executemany("INSERT OR IGNORE INTO brand_groups(food_cd, group_key, is_representative) VALUES (?,?,?)",
                         [(a.food_cd, a.group_key, int(a.representative)) for a in result.grouping.assigned])
        conn.executemany("INSERT INTO category_map(master_id, category, origin, updated_at) VALUES (?,?, 'rule', ?) "
                         "ON CONFLICT(master_id) DO UPDATE SET category=excluded.category, origin='rule', "
                         "updated_at=excluded.updated_at", [(m, c, now) for m, c in by_rule.items()])
        rows_by_id = {r["master_id"]: r for r in result.rows}
        conn.executemany(
            "INSERT INTO category_review(master_id, product_name, maker, food_type, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(master_id) DO UPDATE SET product_name=excluded.product_name, "
            "maker=excluded.maker, food_type=excluded.food_type, last_seen=excluded.last_seen",
            [(m, rows_by_id[m]["product_name"], rows_by_id[m]["maker"], food_type.get(m), now, now) for m in held])
        # 검토 대기에서 이번에 다시 나오지 않은 것(분류가 정해졌거나 원천에서 사라짐)은 지운다
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _held(master_id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _held")
        conn.executemany("INSERT INTO _held VALUES (?)", [(m,) for m in held])
        conn.execute("DELETE FROM category_review WHERE master_id NOT IN (SELECT master_id FROM _held)")

    path, n = write_master(out_rows, csv_dir, allow_second=again)
    rep.file, rep.rows = path, n
    with conn:
        conn.execute("INSERT INTO builds(built_at, file_name, row_count, held_back, seen_until) VALUES (?,?,?,?,?)",
                     (now, path.name, n, len(held), until))
    return rep


# ── seed · 분류 검토 ──────────────────────────────────────────────────────

def seed_categories(conn: sqlite3.Connection, master_csv: Path, *, replace: bool = False) -> tuple[int, int]:
    """product_master.csv(= 지금 RDS)의 category 로 분류표를 채운다. 반환: (전체, 분류 있음)."""
    from .csv_out import read_master
    if not replace and conn.execute("SELECT COUNT(*) FROM category_map").fetchone()[0]:
        raise BuildRefused("분류표가 이미 있습니다. 덮어쓰려면 --replace")
    now = kst_now().strftime(TS)
    rows = [(r["master_id"], (r.get("category") or "").strip() or None, now) for r in read_master(master_csv)
            if r.get("master_id")]
    with conn:
        if replace:
            conn.execute("DELETE FROM category_map")
        conn.executemany("INSERT OR REPLACE INTO category_map(master_id, category, origin, updated_at) "
                         "VALUES (?,?, 'seed', ?)", rows)
    return len(rows), sum(1 for _, c, _ in rows if c)


def seed_groups(conn: sqlite3.Connection, groups_csv: Path, *, replace: bool = False) -> tuple[int, int]:
    """묶음 구성표 CSV (food_cd, group_key, is_representative) 로 묶음을 채운다. 반환: (행, 묶음 수)."""
    if not replace and conn.execute("SELECT COUNT(*) FROM brand_groups").fetchone()[0]:
        raise BuildRefused("묶음 구성이 이미 있습니다. 덮어쓰려면 --replace")
    with open(groups_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        need = {"food_cd", "group_key", "is_representative"}
        if not need <= {h.strip() for h in reader.fieldnames or []}:
            raise BuildRefused(f"묶음 구성표에 {sorted(need)} 열이 있어야 합니다. 헤더: {reader.fieldnames}")
        rows = []
        for r in reader:
            r = {k.strip(): (v or "").strip() for k, v in r.items()}
            if r["food_cd"] and r["group_key"]:
                rows.append((r["food_cd"], r["group_key"], int(r["is_representative"].lower() in ("true", "1", "y"))))
    with conn:
        if replace:
            conn.execute("DELETE FROM brand_groups")
        conn.executemany("INSERT OR REPLACE INTO brand_groups(food_cd, group_key, is_representative) VALUES (?,?,?)", rows)
    return len(rows), len({g for _, g, _ in rows})


REVIEW_COLUMNS = ("master_id", "category", "product_name", "maker", "food_type", "first_seen")
NO_CATEGORY = "-"  # 검토 파일에서 「분류 제외」로 확정할 때 쓰는 값


def export_review(conn: sqlite3.Connection, path: Path) -> int:
    """분류 검토 대기 목록 → CSV. category 칸을 채워 import 한다 (분류 제외로 확정하려면 「-」)."""
    rows = conn.execute("SELECT * FROM category_review ORDER BY master_id").fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        for r in rows:
            w.writerow([r["master_id"], "", r["product_name"], r["maker"], r["food_type"], r["first_seen"]])
    return len(rows)


def import_review(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """채운 검토 CSV → 분류표. category 가 빈 행은 건너뛴다. 반환: (넣은 수, 건너뛴 수)."""
    now = kst_now().strftime(TS)
    done, skipped = [], 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            mid, cat = (r.get("master_id") or "").strip(), (r.get("category") or "").strip()
            if not mid or not cat:
                skipped += 1
                continue
            done.append((mid, None if cat == NO_CATEGORY else cat, now))
    with conn:
        conn.executemany("INSERT INTO category_map(master_id, category, origin, updated_at) VALUES (?,?, 'manual', ?) "
                         "ON CONFLICT(master_id) DO UPDATE SET category=excluded.category, origin='manual', "
                         "updated_at=excluded.updated_at", done)
        conn.executemany("DELETE FROM category_review WHERE master_id=?", [(m,) for m, _, _ in done])
    return len(done), skipped


# ── 두 CSV 비교 ───────────────────────────────────────────────────────────

@dataclass
class DiffReport:
    old_rows: int = 0
    new_rows: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    same: int = 0
    changed: int = 0
    by_column: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # 칸 → (master_id, 이전, 새)


def diff(old_csv: Path, new_csv: Path, max_examples: int = 5) -> DiffReport:
    """master_id 기준으로 두 CSV 를 비교한다. 바뀐 행은 content_hash 가 다른 행, 칸별로 몇 행이 바뀌었는지 센다."""
    from .columns import HASH_COLUMNS
    from .csv_out import read_master
    old = {r["master_id"]: r for r in read_master(old_csv)}
    new = {r["master_id"]: r for r in read_master(new_csv)}
    rep = DiffReport(old_rows=len(old), new_rows=len(new))
    rep.added = sorted(set(new) - set(old))
    rep.removed = sorted(set(old) - set(new))
    for mid in sorted(set(old) & set(new)):
        a, b = old[mid], new[mid]
        if a.get("content_hash") == b.get("content_hash"):
            rep.same += 1
            continue
        rep.changed += 1
        for c in HASH_COLUMNS:
            if (a.get(c) or "") != (b.get(c) or ""):
                rep.by_column[c] = rep.by_column.get(c, 0) + 1
                ex = rep.examples.setdefault(c, [])
                if len(ex) < max_examples:
                    ex.append((mid, a.get(c) or "", b.get(c) or ""))
    return rep