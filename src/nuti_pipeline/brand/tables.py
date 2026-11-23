"""brand 칸 계산용 조회표 (Java BrandLookupTables) — 데이터 팀 CSV 를 메모리로 읽는다 (합쳐 3만 행 남짓).

표는 레포의 tables/brand/ 에 원래 파일 이름 그대로 둔다. 표를 바꾸면 새 날짜 파일을 넣고 커밋한다
(같은 번호의 파일이 여럿이면 이름순으로 마지막 것 = 가장 최근 날짜를 쓴다).

| 파일 | 쓰는 곳 (brand.md 계산 순서) |
| 1_브랜드사전_*.csv           | 3 제품명 첫 낱말 → 브랜드 |
| 2_회사이름표_*.csv           | 7 회사 이름 → 이름표 · 정한 곳 |
| 3_같은회사_합치기표_*.csv    | 8 2단계 합치기 (화면 표시 이름) |
| 4_쿠팡보정_고정값_*.csv      | 2 묶음 키 → 고정 브랜드 |
| fix_1112.csv (선택)          | 1 11-12 수동 고침 (빈 값 = 비움) |
| coupang_names.csv (선택)     | 7 「둘 다」 판정의 쿠팡 상품명 |

필수 파일이 없거나 헤더가 틀리면 ready=False — brand 를 계산하는 쪽은 계산을 거부해야 한다 (틀린 표로 brand 를 조용히 비우지 않게).
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..jtext import jtrim

# 레포 최상위의 tables/brand (src/nuti_pipeline/brand/tables.py 기준 3단계 위). 환경변수 BRAND_TABLES_DIR 로 바꿀 수 있다
DEFAULT_DIR = Path(os.getenv("BRAND_TABLES_DIR") or Path(__file__).resolve().parents[3] / "tables" / "brand")

# 이름표에서 믿을 만한 「정한 곳」 — 이 셋이 아니면 (규칙 · AI 판정(확신 낮음) · 신호 없음 …) 브랜드로 보지 않는다
TRUSTED_DECIDED_BY = frozenset({"10-28 웹 판정", "AI 판정", "브랜드 사전"})
LABEL_BRAND = "브랜드"
LABEL_RETAILER = "유통사"
LABEL_BOTH = "둘 다"


@dataclass(frozen=True)
class CompanyLabel:
    label: str
    decided_by: str

    @property
    def trusted(self) -> bool:
        return self.decided_by in TRUSTED_DECIDED_BY


class TablesError(RuntimeError):
    pass


@dataclass
class BrandTables:
    dictionary: dict[str, str] = field(default_factory=dict)              # 정규화 낱말 → 브랜드
    prefix_words: list[tuple[str, str]] = field(default_factory=list)     # 붙여 써도 쓰는 낱말(2글자 이상), 긴 것부터
    company_labels: dict[str, CompanyLabel] = field(default_factory=dict)  # 회사 이름 → 이름표
    merge2_map: dict[str, str] = field(default_factory=dict)              # 원래 이름 → 보일 이름
    coupang_fix_map: dict[str, str] = field(default_factory=dict)         # 묶음 키 → 브랜드
    fix1112_map: dict[str, str] = field(default_factory=dict)             # 묶음 키 → 브랜드 (빈 문자열 = 비움)
    coupang_names: dict[str, str] = field(default_factory=dict)           # 묶음 키 → 쿠팡 상품명
    problem: str | None = None                                            # 준비 안 된 이유 (준비됐으면 None)
    files: dict[str, str] = field(default_factory=dict)                   # 표 → 읽은 파일 이름

    @property
    def ready(self) -> bool:
        return self.problem is None

    # ── 읽기 ──────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, directory: Path | str = DEFAULT_DIR, optional_dir: Path | str | None = None) -> BrandTables:
        """필수 파일이 없거나 헤더가 틀리면 예외 대신 problem 이 채워진 빈 표를 돌려준다."""
        d = Path(directory)
        od = Path(optional_dir) if optional_dir is not None else d
        t = cls()
        try:
            t._load(d, od)
        except TablesError as e:
            return cls(problem=str(e))
        return t

    def _load(self, d: Path, od: Path) -> None:
        path, rows = _read_latest(d, "1_브랜드사전_*.csv", ("낱말(정규화)", "브랜드(표시 이름)", "붙여 쓴 이름에도 씀"))
        self.files["사전"] = path.name
        for r in rows:
            word, brand = jtrim(r["낱말(정규화)"]), jtrim(r["브랜드(표시 이름)"])
            if not word or not brand:
                continue
            self.dictionary.setdefault(word, brand)  # 같은 낱말이 두 줄이면 값이 같다 — 첫 줄
            if jtrim(r["붙여 쓴 이름에도 씀"]).upper() == "Y" and len(word) >= 2:
                self.prefix_words.append((word, brand))
        self.prefix_words.sort(key=lambda e: (-len(e[0]), e[0]))

        path, rows = _read_latest(d, "2_회사이름표_*.csv", ("이름", "이름표", "정한 곳"))
        self.files["회사 이름표"] = path.name
        for r in rows:
            name = jtrim(r["이름"])
            if name:
                self.company_labels.setdefault(name, CompanyLabel(jtrim(r["이름표"]), jtrim(r["정한 곳"])))

        path, rows = _read_latest(d, "3_같은회사_합치기표_*.csv", ("원래 이름", "보일 이름"))
        self.files["합치기표"] = path.name
        for r in rows:
            frm, to = jtrim(r["원래 이름"]), jtrim(r["보일 이름"])
            if frm and to:
                self.merge2_map.setdefault(frm, to)

        path, rows = _read_latest(d, "4_쿠팡보정_고정값_*.csv", ("영양 묶음 키", "브랜드(지금 값)"))
        self.files["쿠팡 보정"] = path.name
        self.coupang_fix_map = _keyed(rows, "영양 묶음 키", "브랜드(지금 값)", keep_empty=False)

        rows = _read_optional(od / "fix_1112.csv", ("묶음 키", "브랜드"))
        self.fix1112_map = _keyed(rows, "묶음 키", "브랜드", keep_empty=True)
        rows = _read_optional(od / "coupang_names.csv", ("묶음 키", "쿠팡 상품명"))
        self.coupang_names = _keyed(rows, "묶음 키", "쿠팡 상품명", keep_empty=False)

    # ── 조회 ──────────────────────────────────────────────────────────────

    def dictionary_lookup(self, w: str | None) -> str | None:
        """브랜드 사전 (brand.md dictLookup). w 는 compact 한 제품명 첫 낱말.
        사전에 그대로 있으면 그 브랜드. 없으면 「붙여 써도 씀」 낱말을 긴 것부터 보며 w 가 그 낱말로 시작하고 더 길면 그 브랜드."""
        if not w:
            return None
        exact = self.dictionary.get(w)
        if exact is not None:
            return exact
        for word, brand in self.prefix_words:
            if len(w) > len(word) and w.startswith(word):
                return brand
        return None

    def company_label(self, name: str | None) -> CompanyLabel | None:
        """회사 이름표. 키는 정리(clean) · 1단계 합치기를 거친 이름 그대로. 없으면 None (표에 없는 새 회사)."""
        return None if name is None else self.company_labels.get(name)

    def merge2(self, name: str | None) -> str | None:
        """2단계 합치기 — 정확히 같은 이름만 (매일식품 · 해태htb 같은 다른 회사가 있어서 포함 · 앞부분 비교는 안 함)."""
        return None if name is None else self.merge2_map.get(name, name)

    def coupang_fix(self, group_key: str | None) -> str | None:
        """쿠팡 보정 고정값 — 이미 있던 묶음에만 걸린다."""
        return None if group_key is None else self.coupang_fix_map.get(group_key)

    def fix1112(self, group_key: str | None) -> str | None:
        """11-12 수동 고침 — 빈 문자열이면 「비움」(brand None). 표에 없으면 None."""
        return None if group_key is None else self.fix1112_map.get(group_key)

    def coupang_name(self, group_key: str | None) -> str:
        """쿠팡 상품명 — 없으면 빈 문자열 (「둘 다」 판정에서 제품명만 본다)."""
        return "" if group_key is None else self.coupang_names.get(group_key, "")


# ── CSV ───────────────────────────────────────────────────────────────────

def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        records = list(csv.reader(f))
    if not records:
        raise TablesError(f"조회표가 비었습니다: {path.name}")
    header = [h.strip() for h in records[0]]
    missing = [c for c in columns if c not in header]
    if missing:
        raise TablesError(f"{path.name} 에 「{', '.join(missing)}」 열이 없습니다. 헤더: {header}")
    return [{h: (rec[i] if i < len(rec) else "") for i, h in enumerate(header)} for rec in records[1:]]


def _read_latest(d: Path, pattern: str, columns: tuple[str, ...]) -> tuple[Path, list[dict[str, str]]]:
    files = sorted(d.glob(pattern))
    if not files:
        raise TablesError(f"조회표가 없습니다: {d / pattern}")
    return files[-1], _read(files[-1], columns)


def _read_optional(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        return _read(path, columns)
    except TablesError as e:
        if "비었습니다" in str(e):
            return []
        raise


def _keyed(rows: list[dict[str, str]], key_col: str, value_col: str, keep_empty: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows:
        k, v = jtrim(r[key_col]), jtrim(r[value_col])
        if not k or (not keep_empty and not v):
            continue
        out.setdefault(k, v)
    return out
