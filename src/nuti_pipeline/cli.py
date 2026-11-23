"""pipeline 명령. 호스트 cron 이 `docker compose run --rm pipeline <명령>` 으로 띄운다.

  init                      state.db 만들기
  verify-hash <csv>         product_master.csv 의 content_hash 를 다시 계산해 대조
  collect                   (10분마다) 회차 열기 + 이어 받기
  build [--again]           (하루 1회, 07시 전후) 34컬럼 계산 → CSV
  categorize export|import  분류 검토 대기 목록 내보내기 / 채운 파일 넣기
  status                    회차 · 한도 · 마지막 build 요약
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_settings
from .lock import AlreadyRunning, exclusive
from .state import db

log = logging.getLogger("pipeline")


def cmd_init(s, _a) -> int:
    db.connect(s.state_db).close()
    print(f"state.db 준비 완료: {s.state_db}")
    return 0


def cmd_verify_hash(_s, a) -> int:
    from .verify import verify_hash
    rep = verify_hash(Path(a.csv))
    print(f"전체 {rep.total:,}행 / 불일치 {rep.mismatched:,}행")
    for mid, want, got in rep.examples:
        print(f"  {mid}: 파일 {want} / 계산 {got}")
    return 0 if rep.ok else 1


def _not_yet(name: str):
    def run(_s, _a) -> int:
        print(f"{name}: 아직 구현 전", file=sys.stderr)
        return 2
    return run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init, locked=False)

    v = sub.add_parser("verify-hash")
    v.add_argument("csv")
    v.set_defaults(fn=cmd_verify_hash, locked=False)

    sub.add_parser("collect").set_defaults(fn=_not_yet("collect"), locked=True)

    b = sub.add_parser("build")
    b.add_argument("--again", action="store_true", help="오늘 파일이 이미 있으면 _2 를 붙여 다시 만듦")
    b.set_defaults(fn=_not_yet("build"), locked=True)

    c = sub.add_parser("categorize")
    c.add_argument("action", choices=["export", "import"])
    c.add_argument("file", nargs="?")
    c.set_defaults(fn=_not_yet("categorize"), locked=True)

    sub.add_parser("status").set_defaults(fn=_not_yet("status"), locked=False)

    a = p.parse_args(argv)
    s = load_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if not a.locked:
        return a.fn(s, a)
    try:
        with exclusive(s.lock_file):
            return a.fn(s, a)
    except AlreadyRunning as e:
        log.warning("%s — 이번 실행은 건너뜀", e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
