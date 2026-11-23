"""CSV 출력 — Java 쪽과의 약속.

- 위치: {DATA_DIR}/csv/product_master_YYYYMMDD.csv, UTF-8(BOM 없음), 34컬럼
- 매 파일은 전체 제품 (변경분 아님)
- .tmp 로 다 쓴 뒤 .csv 로 이름 변경 → api 는 .csv 만 읽으면 된다
- content_hash 는 호출한 쪽 값을 믿지 않고 여기서 항상 다시 계산
"""
from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path

from . import content_hash
from .columns import MASTER_COLUMNS
from .config import KST

PREFIX = "product_master_"


def target_path(csv_dir: Path, day: date, *, allow_second: bool = False) -> Path:
    """그날 파일 경로. 이미 있으면 기본은 거부, allow_second 면 _2, _3 … 을 붙인다."""
    base = csv_dir / f"{PREFIX}{day:%Y%m%d}.csv"
    if not base.exists():
        return base
    if not allow_second:
        raise FileExistsError(f"오늘 파일이 이미 있습니다: {base.name} (다시 만들려면 --again)")
    n = 2
    while (p := csv_dir / f"{PREFIX}{day:%Y%m%d}_{n}.csv").exists():
        n += 1
    return p


def write_master(rows: Iterable[Mapping[str, str | None]], csv_dir: Path, *,
                 day: date | None = None, allow_second: bool = False) -> tuple[Path, int]:
    """rows: 34컬럼 중 master_id + 30칸을 문자열로 맞춘 dict. 반환: (최종 경로, 행 수)."""
    csv_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    final = target_path(csv_dir, day or now.date(), allow_second=allow_second)
    tmp = final.with_suffix(".csv.tmp")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")

    count = 0
    seen: set[str] = set()
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(MASTER_COLUMNS)
            for row in rows:
                mid = row.get("master_id")
                if not mid:
                    raise ValueError(f"master_id 가 없는 행: {dict(row)}")
                if mid in seen:
                    raise ValueError(f"master_id 중복: {mid}")
                seen.add(mid)
                out = {c: (row.get(c) or "") for c in MASTER_COLUMNS}
                out["content_hash"] = content_hash.compute(row)
                out["created_at"] = out["updated_at"] = stamp  # Java 는 이 두 칸을 읽지 않음 (추적용)
                w.writerow(out[c] for c in MASTER_COLUMNS)
                count += 1
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return final, count


def read_master(path: Path) -> Iterable[dict[str, str]]:
    """product_master.csv 읽기 (BOM 있어도 됨). 헤더 앞뒤 공백 제거."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        header = [h.strip() for h in next(r)]
        for rec in r:
            yield dict(zip(header, rec))
