"""product_master.csv 대조 — 파일의 content_hash 를 다시 계산해 100% 같은지 본다."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import content_hash
from .csv_out import read_master


@dataclass
class HashReport:
    total: int = 0
    mismatched: int = 0
    examples: list[tuple[str, str, str]] = field(default_factory=list)  # (master_id, 파일 값, 계산 값)

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.mismatched == 0


def verify_hash(path: Path, max_examples: int = 10) -> HashReport:
    rep = HashReport()
    for row in read_master(path):
        rep.total += 1
        got = content_hash.compute(row)
        if got != row.get("content_hash", ""):
            rep.mismatched += 1
            if len(rep.examples) < max_examples:
                rep.examples.append((row.get("master_id", ""), row.get("content_hash", ""), got))
    return rep
