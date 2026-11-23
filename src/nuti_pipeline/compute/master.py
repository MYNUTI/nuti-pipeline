"""원본 → 34컬럼 (Java MasterBuilder, sync.md 「컬럼별 채우는 곳」). 같은 원본이면 같은 결과 (순서 · 동점은 정렬로 고정).

  1. 행 고르기     영양성분DB(가공식품 · 상용제품) + 푸드QR(한국어 · 바코드당 최신 판, 영양성분DB 와 보고번호 · 바코드가 겹치면 뺌)
  2. 칸 채우기     report_no · 제품명 · 제조사 · 총내용량 · 영양성분 · 원재료 · 생산중단
  3. barcode      푸드QR 자기 바코드 > 보고번호로 푸드QR(영양+제조) > HACCP > I0490 > C005 > I2570,
                  없으면 제품명 + 제조사 (HACCP > C005 > I2570). 한 바코드가 여러 행에 걸리면 생산 중 1개일 때만 남김
  4. brand        영양성분DB 행만 — 묶음 대표 행 + 묶음 바코드 주인으로 brand_fn (brand 단계에서 BrandResolver 를 넣는다)
  5. category     categories 에서 가져옴 (분류 단계에서 분류표 · 규칙으로 채운 값을 넣는다)
  6. content_hash

입력 행은 받은 순서(먼저 → 나중)로 넣는다. 같은 키가 여러 번 나오면 나중 것을 쓴다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .. import content_hash
from ..brand.names import NameRules, compact
from ..columns import NUTRIENT_COLUMNS
from ..jtext import jstrip
from . import grouping as grp
from .rules import (NUTRI_DB_FIELDS, NUTRIENTS, Amount, Basis, amount, amount_of, basis, digits, foodqr_nutrient_index,
                    format_number, gtin14, number, per100, report_no, text, whole)

Row = Mapping[str, str | None]

SOURCE_NUTRI_DB = "nutri_db"
SOURCE_FOODQR = "foodqr"
FOODQR_PREFIX = "FQR-"
KOREAN = "001"
HACCP_UNKNOWN = "알수없음"
_BAD_OWNER = re.compile(r"\d|특별시|광역시|www\.", re.ASCII)  # 바코드 주인에서 뺄 이름 (brand.md barcodeOwner)


@dataclass(frozen=True)
class BrandInput:
    """묶음 하나의 brand 계산 입력 (Java BrandResolver.Input)."""
    group_key: str
    product_name: str | None
    seller: str | None
    maker: str | None
    importer: str | None
    barcode_owner: str | None


@dataclass
class MasterInput:
    """원본에서 꺼낸 행(필드명 → 값)과 파이프라인이 보관하는 값."""
    nutri_db: Sequence[Row] = ()
    foodqr_list: Sequence[Row] = ()
    foodqr_nutrition: Sequence[Row] = ()
    foodqr_maker: Sequence[Row] = ()
    haccp: Sequence[Row] = ()
    c005: Sequence[Row] = ()
    i2570: Sequence[Row] = ()
    c002: Sequence[Row] = ()
    c006: Sequence[Row] = ()
    i0490: Sequence[Row] = ()
    discontinued: frozenset[str] = frozenset()               # I2852 품목보고번호 (report_no 로 맞춘 값)
    categories: Mapping[str, str | None] = field(default_factory=dict)  # master_id → 분류 코드
    stored_groups: Mapping[str, grp.Stored] = field(default_factory=dict)  # food_cd → 저장된 묶음


@dataclass
class Stats:
    nutri_rows: int = 0
    foodqr_rows: int = 0
    foodqr_excluded: int = 0
    with_barcode: int = 0
    barcode_by_name: int = 0
    barcode_cleared: int = 0
    with_ingredients: int = 0
    discontinued: int = 0
    with_brand: int = 0
    with_category: int = 0
    new_products: int = 0
    new_groups: int = 0


@dataclass
class MasterResult:
    rows: list[dict[str, str | None]]          # master_id ~ content_hash 32칸. 값은 hash 와 같은 표기, 빈 값은 None
    grouping: grp.GroupingResult                # 새로 정한 묶음(assigned)은 저장해야 한다
    stats: Stats
    new_without_category: list[str]             # 분류가 없는 새 제품 master_id


class _Row:
    __slots__ = ("master_id", "source", "report_no", "name", "maker", "ingredients", "category", "brand",
                 "barcode", "serving", "per100", "discontinued", "nutri", "own_barcode")

    def __init__(self) -> None:
        self.report_no = self.name = self.maker = self.ingredients = self.category = self.brand = None
        self.barcode = self.own_barcode = None
        self.serving: Amount | None = None
        self.per100: list[float | None] = [None] * NUTRIENTS
        self.discontinued = False
        self.nutri: Row | None = None  # 영양성분DB 원본(브랜드용), 푸드QR 은 None


def _no_brand(_: BrandInput) -> str | None:
    return None


class MasterBuilder:
    def __init__(self, rules: NameRules | None = None,
                 brand_fn: Callable[[BrandInput], str | None] = _no_brand) -> None:
        """rules: 회사 이름 정리(clean) — 바코드 주인 · 제품명+제조사 바코드 찾기에 쓴다. 지명 목록이 들어간 것.
        brand_fn: 묶음 하나의 brand (brand 단계의 BrandResolver.resolve)."""
        self.rules = rules or NameRules()
        self.brand_fn = brand_fn
        self._clean_cache: dict[str, str] = {}

    # ── 전체 ──────────────────────────────────────────────────────────────

    def build(self, inp: MasterInput) -> MasterResult:
        self._clean_cache.clear()
        stats = Stats()

        # 1·2. 영양성분DB 행
        nutri_by_id: dict[str, Row] = {}
        for r in inp.nutri_db:
            fid = text(r.get("FOOD_CD"))
            if fid is None:
                continue
            if text(r.get("DB_GRP_NM")) != "가공식품" or text(r.get("DB_CLASS_NM")) != "상용제품":
                continue
            nutri_by_id[fid] = r
        nutri_rows: list[_Row] = []
        for fid, r in nutri_by_id.items():
            row = _Row()
            row.master_id = fid
            row.source = SOURCE_NUTRI_DB
            row.report_no = report_no(r.get("ITEM_REPORT_NO"))
            row.name = text(r.get("FOOD_NM_KR"))
            row.maker = text(r.get("MAKER_NM"))
            row.serving = amount(r.get("Z10500"))
            row.per100 = [number(r.get(f)) for f in NUTRI_DB_FIELDS]
            row.nutri = r
            nutri_rows.append(row)

        # 3. 바코드 찾기 표
        by_report = [
            _barcodes_by_report([*inp.foodqr_nutrition, *inp.foodqr_maker], "IMRPT_NO", "BRCD_NO"),
            _barcodes_by_report(inp.haccp, "prdlstReportNo", "barcode"),
            _barcodes_by_report(inp.i0490, "PRDLST_REPORT_NO", "BRCDNO"),
            _barcodes_by_report(inp.c005, "PRDLST_REPORT_NO", "BAR_CD"),
            _barcodes_by_report(inp.i2570, "PRDLST_REPORT_NO", "BRCD_NO"),
        ]
        by_name = [
            self._barcodes_by_name(inp.haccp, "prdlstNm", "manufacture", "barcode"),
            self._barcodes_by_name(inp.c005, "PRDLST_NM", "BSSH_NM", "BAR_CD"),
            self._barcodes_by_name(inp.i2570, "PRDT_NM", "CMPNY_NM", "BRCD_NO"),
        ]

        nutri_reports: set[str] = set()
        nutri_barcodes: set[str] = set()
        for row in nutri_rows:
            row.barcode = self._find_barcode(row, by_report, by_name, stats)
            if row.report_no is not None:
                nutri_reports.add(row.report_no)
            if row.barcode is not None:
                nutri_barcodes.add(row.barcode)

        # 1·2. 푸드QR 행
        foodqr_rows = self._foodqr_rows(inp, by_report, by_name, nutri_reports, nutri_barcodes, stats)
        all_rows = nutri_rows + foodqr_rows

        # 원재료 · 생산중단
        c002 = _latest_ingredients(inp.c002)
        c006 = _latest_ingredients(inp.c006)
        haccp: dict[str, str] = {}
        for r in inp.haccp:
            rn = report_no(r.get("prdlstReportNo"))
            raw = text(r.get("rawmtrl"))
            if rn is not None and raw is not None and raw != HACCP_UNKNOWN:
                haccp[rn] = raw
        for row in all_rows:
            if row.report_no is None:
                continue
            row.ingredients = c002.get(row.report_no) or c006.get(row.report_no) or haccp.get(row.report_no)
            row.discontinued = row.report_no in inp.discontinued

        # 3. 한 바코드가 여러 행에 걸리면
        stats.barcode_cleared = resolve_barcode_conflicts(all_rows)

        # 4. brand
        grouping = self._assign_brands(nutri_rows, inp)

        # 5·6. 분류 · 출력
        out: list[dict[str, str | None]] = []
        new_without_category: list[str] = []
        for row in all_rows:
            known = row.master_id in inp.categories
            row.category = text(inp.categories.get(row.master_id))
            if not known:
                stats.new_products += 1
                if row.category is None:
                    new_without_category.append(row.master_id)
            out.append(_to_map(row))
            stats.with_barcode += row.barcode is not None
            stats.with_ingredients += row.ingredients is not None
            stats.discontinued += row.discontinued
            stats.with_brand += row.brand is not None
            stats.with_category += row.category is not None
        new_without_category.sort()
        stats.nutri_rows = len(nutri_rows)
        stats.foodqr_rows = len(foodqr_rows)
        stats.new_groups = sum(1 for a in grouping.assigned if a.representative)
        return MasterResult(out, grouping, stats, new_without_category)

    # ── 푸드QR ────────────────────────────────────────────────────────────

    def _foodqr_rows(self, inp: MasterInput, by_report, by_name, nutri_reports: set[str], nutri_barcodes: set[str],
                     stats: Stats) -> list[_Row]:
        # 한국어 판만, 라벨관리번호당 마지막 줄
        by_elb: dict[str, Row] = {}
        for r in inp.foodqr_list:
            elb = text(r.get("ELB_MNG_NO"))
            if elb is None or text(r.get("LGG_CD")) != KOREAN:
                continue
            by_elb[elb] = r
        # 바코드당 최신 판 1개 (VLD_BGNG_YMD, VER_NO 최신). 바코드 없는 판은 모두 남긴다
        latest_by_barcode: dict[str, Row] = {}
        for r in by_elb.values():
            bc = text(r.get("BRCD_NO"))
            if bc is None:
                continue
            cur = latest_by_barcode.get(bc)
            if cur is None or _newer(r, cur):
                latest_by_barcode[bc] = r
        nutrition: dict[str, list[Row]] = defaultdict(list)
        for r in inp.foodqr_nutrition:
            elb = text(r.get("ELB_MNG_NO"))
            if elb is not None:
                nutrition[elb].append(r)
        makers: dict[str, Row] = {}
        for r in inp.foodqr_maker:
            elb = text(r.get("ELB_MNG_NO"))
            if elb is not None:
                makers[elb] = r

        rows: list[_Row] = []
        for elb, r in by_elb.items():
            bc = text(r.get("BRCD_NO"))
            if bc is not None and latest_by_barcode.get(bc) is not r:
                continue
            nut = _first_display_group(nutrition.get(elb, []))
            mk = makers.get(elb)
            row = _Row()
            row.master_id = FOODQR_PREFIX + elb
            row.source = SOURCE_FOODQR
            for n in nut:
                row.report_no = report_no(n.get("IMRPT_NO"))
                if row.report_no is not None:
                    break
            if row.report_no is None and mk is not None:
                row.report_no = report_no(mk.get("IMRPT_NO"))
            row.name = text(r.get("PRDCT_NM"))
            row.maker = text(mk.get("BUES_NM")) if mk is not None else None
            if row.maker is None:
                row.maker = text(r.get("BZENTY_NM"))
            row.serving = amount(r.get("CTV"))
            if row.serving is None and nut:
                row.serving = amount_of(nut[0].get("NTRTN_INDCT_TCT"), nut[0].get("NTRTN_INDCT_TCD"))
            _fill_foodqr_nutrients(row, nut)
            row.own_barcode = gtin14(bc)
            row.barcode = self._find_barcode(row, by_report, by_name, stats)

            # 영양성분DB 와 보고번호 · 바코드가 겹치면 영양성분DB 쪽을 쓴다
            if (row.report_no is not None and row.report_no in nutri_reports) or \
                    (row.barcode is not None and row.barcode in nutri_barcodes):
                stats.foodqr_excluded += 1
                continue
            rows.append(row)
        return rows

    # ── 바코드 ────────────────────────────────────────────────────────────

    def _find_barcode(self, row: _Row, by_report, by_name, stats: Stats) -> str | None:
        if row.own_barcode is not None:
            return row.own_barcode
        if row.report_no is not None:
            for m in by_report:
                s = m.get(row.report_no)
                if s:
                    return min(s)
        key = self.name_key(row.name, row.maker)
        if key is not None:
            for m in by_name:
                s = m.get(key)
                if s:
                    stats.barcode_by_name += 1
                    return min(s)
        return None

    def _barcodes_by_name(self, rows: Sequence[Row], name_f: str, maker_f: str, bc_f: str) -> dict[str, set[str]]:
        m: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            g = gtin14(r.get(bc_f))
            if g is None:
                continue
            key = self.name_key(r.get(name_f), r.get(maker_f))
            if key is not None:
                m[key].add(g)
        return m

    def name_key(self, name: str | None, maker: str | None) -> str | None:
        """제품명 + 제조사 일치 키 — compact(제품명) | compact(clean(제조사)). 둘 중 하나라도 비면 None."""
        n = compact(text(name))
        mk = text(maker)
        if not n or mk is None:
            return None
        c = compact(self._clean(mk))
        return f"{n}|{c}" if c else None

    # ── brand ─────────────────────────────────────────────────────────────

    def _assign_brands(self, nutri_rows: list[_Row], inp: MasterInput) -> grp.GroupingResult:
        # 880 바코드 앞 9자리 (보고번호별, C005)
        prefixes_by_report: dict[str, set[str]] = defaultdict(set)
        for r in inp.c005:
            rn = report_no(r.get("PRDLST_REPORT_NO"))
            p = prefix880(r.get("BAR_CD"))
            if rn is not None and p is not None:
                prefixes_by_report[rn].add(p)
        # 880 번호 주인표 (brand.md 조회표 4) — 판매원이 있는 행만, 앞 9자리별 판매원 다수결
        votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in nutri_rows:
            seller = text(row.nutri.get("SELLER_MANUFAC_NM"))
            if seller is None or row.report_no is None:
                continue
            owner = self._clean(seller)
            for p in prefixes_by_report.get(row.report_no, ()):
                votes[p][owner] += 1
        owner_of_prefix = {p: _most_common(v) for p, v in votes.items()}

        # 묶음
        members = [grp.Member(r.master_id, r.nutri.get("FOOD_NM_KR"), r.nutri.get("MAKER_NM"), r.nutri.get("Z10500"))
                   for r in nutri_rows]
        by_id = {r.master_id: r for r in nutri_rows}
        grouping = grp.assign(members, inp.stored_groups)

        rows_of_group: dict[str, list[_Row]] = defaultdict(list)
        for row in nutri_rows:
            rows_of_group[grouping.group_of[row.master_id]].append(row)

        for g, rows in rows_of_group.items():
            rep = by_id[grouping.representative_of[g]]
            owners: dict[str, int] = defaultdict(int)
            seen: set[str] = set()
            for row in rows:
                if row.report_no is None:
                    continue
                for p in sorted(prefixes_by_report.get(row.report_no, ())):
                    if p in seen:
                        continue
                    seen.add(p)
                    o = owner_of_prefix.get(p)
                    if o is not None and not _BAD_OWNER.search(o):
                        owners[o] += 1
            r = rep.nutri
            brand = self.brand_fn(BrandInput(g, r.get("FOOD_NM_KR"), r.get("SELLER_MANUFAC_NM"), r.get("MAKER_NM"),
                                             r.get("IMP_MANUFAC_NM"), _most_common(owners) if owners else None))
            for row in rows:
                row.brand = text(brand)
        return grouping

    def _clean(self, raw: str) -> str:
        c = self._clean_cache.get(raw)
        if c is None:
            c = self.rules.clean(raw)
            if not c or not jstrip(c):
                c = jstrip(raw)
            self._clean_cache[raw] = c
        return c


# ── 함수 ──────────────────────────────────────────────────────────────────

def _newer(a: Row, b: Row) -> bool:
    """a 가 b 보다 새 판인가 — VLD_BGNG_YMD, 같으면 VER_NO."""
    da, db = digits(a.get("VLD_BGNG_YMD")), digits(b.get("VLD_BGNG_YMD"))
    if da != db:
        return da > db
    va, vb = number(a.get("VER_NO")), number(b.get("VER_NO"))
    return (-1 if va is None else va) > (-1 if vb is None else vb)


def _first_display_group(rows: list[Row]) -> list[Row]:
    """영양 표시 묶음이 여럿이면 NTRTN_INDCT_SN 가장 작은 것."""
    sns = [number(r.get("NTRTN_INDCT_SN")) for r in rows]
    valid = [s for s in sns if s is not None]
    if not valid:
        return rows
    mn = min(valid)
    return [r for r, s in zip(rows, sns) if s is not None and s == mn]


def _fill_foodqr_nutrients(row: _Row, nut: list[Row]) -> None:
    for n in nut:
        idx = foodqr_nutrient_index(n.get("NIRWMT_NM"))
        v = number(n.get("CTA"))
        if idx < 0 or v is None or row.per100[idx] is not None:
            continue
        basis_amount = amount_of(n.get("NTRTN_INDCT_TCT"), n.get("NTRTN_INDCT_TCD"))
        basis_number = basis_amount.value if basis_amount is not None else number(n.get("NTRTN_INDCT_TCT"))
        b = basis(n.get("NTRTN_INDCT_CD"), basis_number)
        if b is Basis.PER_100:
            amt = None
        elif b is Basis.TOTAL:
            amt = basis_number if basis_number is not None else (row.serving.value if row.serving else None)
        else:
            unit = amount_of(n.get("NTRTN_ICUT_CTV"), n.get("NTRTN_ICUT_CVUCD"))
            u = unit.value if unit is not None else number(n.get("NTRTN_ICUT_CTV"))
            amt = u if u is not None else basis_number
        row.per100[idx] = per100(v, b, amt)


def _barcodes_by_report(rows: Sequence[Row], report_f: str, bc_f: str) -> dict[str, set[str]]:
    m: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        rn = report_no(r.get(report_f))
        g = gtin14(r.get(bc_f))
        if rn is not None and g is not None:
            m[rn].add(g)
    return m


def resolve_barcode_conflicts(rows: list[_Row]) -> int:
    """한 바코드가 여러 행에 걸리면: 생산 중 2개 이상 → 모두 비움, 생산 중 1개 → 그 행만 남김, 생산중단끼리 → 모두 비움.
    반환: 비운 행 수."""
    by_barcode: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        if r.barcode is not None:
            by_barcode[r.barcode].append(r)
    cleared = 0
    for holders in by_barcode.values():
        if len(holders) < 2:
            continue
        active = [r for r in holders if not r.discontinued]
        for r in holders:
            if len(active) == 1 and active[0] is r:
                continue
            r.barcode = None
            cleared += 1
    return cleared


def _latest_ingredients(rows: Sequence[Row]) -> dict[str, str]:
    """보고번호 → 원재료. 같은 보고번호가 여럿이면 CHNG_DT 최신 (같으면 나중에 받은 것)."""
    txt: dict[str, str] = {}
    date: dict[str, str] = {}
    for r in rows:
        rn = report_no(r.get("PRDLST_REPORT_NO"))
        raw = text(r.get("RAWMTRL_NM"))
        if rn is None or raw is None:
            continue
        d = digits(r.get("CHNG_DT"))
        prev = date.get(rn)
        if prev is None or d >= prev:
            date[rn] = d
            txt[rn] = raw
    return txt


def prefix880(raw: str | None) -> str | None:
    """880 으로 시작하는 바코드(13자리 기준)의 앞 9자리. 아니면 None."""
    d = digits(raw)
    if len(d) == 14 and d[0] == "0":
        d = d[1:]
    return d[:9] if len(d) >= 9 and d.startswith("880") else None


def _most_common(counts: Mapping[str, int]) -> str:
    """가장 많은 것. 같으면 이름 순으로 앞."""
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _to_map(row: _Row) -> dict[str, str | None]:
    m: dict[str, str | None] = {
        "master_id": row.master_id,
        "barcode": row.barcode,
        "report_no": row.report_no,
        "source": row.source,
        "product_name": row.name,
        "brand": row.brand,
        "category": row.category,
        "maker": row.maker,
        "ingredients": row.ingredients,
        "serving_size": row.serving.format() if row.serving else None,
    }
    for i, c in enumerate(NUTRIENT_COLUMNS):
        m[c] = format_number(whole(row.per100[i], row.serving))
    for i, c in enumerate(NUTRIENT_COLUMNS):
        m[c + "_per_100g"] = format_number(row.per100[i])
    m["is_discontinued"] = content_hash.format_bool(row.discontinued)
    m["content_hash"] = content_hash.compute(m)
    return m
