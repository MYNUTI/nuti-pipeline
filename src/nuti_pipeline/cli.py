"""pipeline 명령. 서버에서는 호스트 cron 이 `docker compose run --rm pipeline <명령>` 으로 띄운다.

  init                              state.db 만들기
  verify-hash <csv>                 product_master.csv 의 content_hash 를 다시 계산해 대조
  probe <API> [--page N] [--cond V] [--raw] [--param 이름=값]
                                    API 한 페이지만 불러 응답 확인 (저장 안 함, 호출 수에는 셈)
  collect                           (cron 10분마다) 주기가 된 회차 열기 + 받는 중인 회차 이어 받기
  round start <API> <FULL|INCREMENTAL> [조건]   회차 손으로 열기
  round step <회차ID> [--max N]                 회차 손으로 이어 받기 (밤 제한 무시, 한도는 지킴)
  round cancel <회차ID>                         회차 취소 (받은 행도 지움)
  status                            API 별 회차 · 원본 행 수 · 오늘 호출 수
  build [--again]                   (하루 1회, 07시 전후) 34컬럼 계산 → CSV        ※ 아직 구현 전
  categorize export|import          분류 검토 대기 목록 내보내기 / 넣기           ※ 아직 구현 전
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .apis import APIS
from .config import Settings, load_settings
from .lock import AlreadyRunning, exclusive
from .state import db

log = logging.getLogger("pipeline")


# ── 공용 ──────────────────────────────────────────────────────────────────

def _collector(s: Settings):
    from .collect.clients import Http, make_clients
    from .collect.collector import Collector
    s.require_keys()
    conn = db.connect(s.state_db)
    http = Http()
    return Collector(conn, make_clients(http, s.foodsafety_key, s.datagokr_service_key), s.datagokr_daily_limit), http


def _api_name(name: str) -> str:
    n = name.upper()
    if n not in APIS:
        raise SystemExit(f"없는 API 입니다: {name} (가능: {', '.join(APIS)})")
    return n


# ── 명령 ──────────────────────────────────────────────────────────────────

def cmd_init(s: Settings, _a) -> int:
    db.connect(s.state_db).close()
    print(f"state.db 준비 완료: {s.state_db}")
    return 0


def cmd_verify_hash(_s: Settings, a) -> int:
    from .verify import verify_hash
    rep = verify_hash(Path(a.csv))
    print(f"전체 {rep.total:,}행 / 불일치 {rep.mismatched:,}행")
    for mid, want, got in rep.examples:
        print(f"  {mid}: 파일 {want} / 계산 {got}")
    return 0 if rep.ok else 1


def cmd_probe(s: Settings, a) -> int:
    from .collect.collector import _ts
    c, http = _collector(s)
    try:
        api = APIS[_api_name(a.api)]
        extra = dict(kv.split("=", 1) for kv in a.param)
        client = c.clients[api.provider]
        if extra and api.provider.value != "DATA_GO_KR":
            raise SystemExit("--param 은 공공데이터포털 API 에만 쓸 수 있습니다")
        page = client.fetch(api, a.page, a.cond, extra) if extra else client.fetch(api, a.page, a.cond)
        with c.conn:
            c.conn.execute("INSERT INTO call_log(api, called_at, code) VALUES (?,?,?)", (api.name, _ts(c.now()), page.code))
        print(f"{api.name} ({api.label}) {a.page}페이지")
        print(f"  결과: {page.status.value}  코드: {page.code}  메시지: {page.message}")
        print(f"  전체 행 수: {page.total_count}  이번 페이지 행 수: {len(page.rows)}")
        if page.rows:
            print("  첫 행:")
            print(json.dumps(page.rows[0], ensure_ascii=False, indent=2))
        if a.raw:
            print(f"  응답 본문 앞 {a.raw}자:")
            print(page.raw[: a.raw])
        return 0 if page.status.value in ("OK", "NO_DATA") else 1
    finally:
        http.close()


def cmd_collect(s: Settings, _a) -> int:
    c, http = _collector(s)
    try:
        res = c.tick()
    finally:
        http.close()
    for title, items in (("연 회차", res.started), ("이어 받은 회차", res.stepped),
                         ("건너뜀", res.skipped), ("오류", res.errors)):
        if items:
            print(f"[{title}]")
            for x in items:
                print(f"  {x}")
    if not (res.started or res.stepped or res.skipped or res.errors):
        print("할 일이 없습니다 (주기가 된 API 도, 받는 중인 회차도 없음)")
    return 1 if res.errors else 0


def cmd_round(s: Settings, a) -> int:
    from .collect.collector import CollectError
    from .collect.rules import Mode
    c, http = _collector(s)
    try:
        if a.action == "start":
            rid = c.start(_api_name(a.target), Mode(a.mode.upper()), a.cond)
            print(f"회차 {rid} 시작. 이어 받기: pipeline round step {rid}")
        elif a.action == "step":
            r = c.step(int(a.target), a.max)
            print(f"회차 {r['id']} {r['api']}: {r['status']} 받은 {r['rows_received']}행 / 전체 {r['total_count']} "
                  f"다음 {r['next_page']}페이지 ({r['last_code']} {r['last_message']})")
        elif a.action == "cancel":
            c.cancel(int(a.target))
            print(f"회차 {a.target} 취소, 받은 행 삭제")
        return 0
    except (CollectError, ValueError) as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    finally:
        http.close()


def cmd_status(s: Settings, _a) -> int:
    from .collect.collector import Collector
    conn = db.connect(s.state_db)
    c = Collector(conn, {}, s.datagokr_daily_limit)
    print(f"{'API':<17}{'마지막 회차':<44}{'마지막 전량 완료':<22}{'원본 행':>12}{'오늘 호출':>12}")
    for api in APIS.values():
        last = conn.execute("SELECT * FROM rounds WHERE api=? ORDER BY id DESC LIMIT 1", (api.name,)).fetchone()
        full = conn.execute("SELECT finished_at FROM rounds WHERE api=? AND mode='FULL' AND status='DONE' "
                            "ORDER BY id DESC LIMIT 1", (api.name,)).fetchone()
        raw = conn.execute("SELECT COUNT(*) FROM raw WHERE api=?", (api.name,)).fetchone()[0]
        last_s = (f"#{last['id']} {last['mode'][:4]} {last['status']} {last['rows_received']}/{last['total_count']}"
                  if last else "-")
        print(f"{api.name:<17}{last_s:<44}{(full['finished_at'] if full else '-'):<22}{raw:>12,}"
              f"{c.calls_today(api):>6}/{c.daily_limit(api):<5}")
    return 0


def _not_yet(name: str):
    def run(_s, _a) -> int:
        print(f"{name}: 아직 구현 전", file=sys.stderr)
        return 2
    return run


# ── 진입점 ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init, locked=False)

    v = sub.add_parser("verify-hash")
    v.add_argument("csv")
    v.set_defaults(fn=cmd_verify_hash, locked=False)

    pr = sub.add_parser("probe")
    pr.add_argument("api")
    pr.add_argument("--page", type=int, default=1)
    pr.add_argument("--cond", help="CHNG_DT=YYYYMMDD 또는 EVL_YR=YYYY 의 값")
    pr.add_argument("--raw", type=int, nargs="?", const=2000, default=0,
                    help="응답 본문을 그대로 보여 줌 (기본 앞 2000자)")
    pr.add_argument("--param", action="append", default=[], metavar="이름=값",
                    help="공공데이터포털 요청에 파라미터 추가 (확인용, 여러 번 가능)")
    pr.set_defaults(fn=cmd_probe, locked=False)

    sub.add_parser("collect").set_defaults(fn=cmd_collect, locked=True)

    r = sub.add_parser("round")
    r.add_argument("action", choices=["start", "step", "cancel"])
    r.add_argument("target", help="start: API 이름 / step · cancel: 회차 ID")
    r.add_argument("mode", nargs="?", default="FULL", help="start 때 FULL 또는 INCREMENTAL")
    r.add_argument("cond", nargs="?", help="start 때 조건 값 (증분 YYYYMMDD / 생산실적 YYYY)")
    r.add_argument("--max", type=int, default=100, help="step 때 최대 호출 수")
    r.set_defaults(fn=cmd_round, locked=True)

    sub.add_parser("status").set_defaults(fn=cmd_status, locked=False)

    b = sub.add_parser("build")
    b.add_argument("--again", action="store_true", help="오늘 파일이 이미 있으면 _2 를 붙여 다시 만듦")
    b.set_defaults(fn=_not_yet("build"), locked=True)

    c = sub.add_parser("categorize")
    c.add_argument("action", choices=["export", "import"])
    c.add_argument("file", nargs="?")
    c.set_defaults(fn=_not_yet("categorize"), locked=True)

    a = p.parse_args(argv)
    s = load_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx 는 INFO 로그에 요청 URL 을 그대로 찍는다 — URL 에 인증키가 들어 있으므로 끈다
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

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