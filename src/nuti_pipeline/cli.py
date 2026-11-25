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
  seed categories <product_master.csv>   지금 RDS 와 같은 분류로 분류표 채우기 (처음 한 번)
  seed groups <묶음 구성표.csv>          묶음 구성 채우기 (처음 한 번)
  build [--again] [--force] [--dry-run]  (하루 1회, 07시 전후) 34컬럼 계산 → CSV
  categorize export|import [파일]        분류 검토 대기 목록 내보내기 / 채운 파일 넣기
  diff <이전.csv> <새.csv>               두 CSV 에서 바뀐 행 · 칸 비교
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
    from . import build
    print()
    last = conn.execute("SELECT * FROM builds ORDER BY id DESC LIMIT 1").fetchone()
    print(f"마지막 build: {last['built_at']} {last['file_name']} ({last['row_count']:,}행, 분류 대기 {last['held_back']:,})"
          if last else "마지막 build: 없음")
    print(f"분류표 {conn.execute('SELECT COUNT(*) FROM category_map').fetchone()[0]:,}행 · "
          f"묶음 {conn.execute('SELECT COUNT(*) FROM brand_groups').fetchone()[0]:,}행 · "
          f"분류 검토 대기 {conn.execute('SELECT COUNT(*) FROM category_review').fetchone()[0]:,}개")
    blk = build.blockers(conn)
    print("build 가능" if not blk else "build 를 막는 것 (전량을 끝까지 받은 적 없음): " + ", ".join(b.split("(")[0] for b in blk))
    return 0



def cmd_build(s: Settings, a) -> int:
    from . import build
    conn = db.connect(s.state_db)
    try:
        rep = build.run(conn, s.csv_dir, force=a.force, again=a.again, dry_run=a.dry_run)
    except (build.BuildRefused, FileExistsError) as e:
        print(f"만들지 않음: {e}", file=sys.stderr)
        return 1
    if rep.skipped:
        print("CSV 를 만들지 않았습니다:")
        for x in rep.skipped:
            print(f"  {x}")
        return 0
    st = rep.stats
    print(f"{'미리보기' if a.dry_run else 'CSV'}: {rep.file} ({rep.rows:,}행)")
    print(f"  영양성분DB {st.nutri_rows:,} · 푸드QR {st.foodqr_rows:,} (영양성분DB 와 겹쳐 뺀 푸드QR {st.foodqr_excluded:,})")
    print(f"  바코드 {st.with_barcode:,} (제품명+제조사로 찾음 {st.barcode_by_name:,}, 겹쳐서 비움 {st.barcode_cleared:,})")
    print(f"  원재료 {st.with_ingredients:,} · 생산중단 {st.discontinued:,} · 브랜드 {st.with_brand:,} · 분류 {st.with_category:,}")
    print(f"  새 제품 {st.new_products:,} · 새 묶음 {rep.new_groups:,} · 지명 {rep.place_names:,}개")
    print(f"  쿠팡 보정 키 일치 {rep.coupang_matched:,}/{rep.coupang_total:,} · 11-12 고침 키 일치 {rep.fix_matched:,}/{rep.fix_total:,}"
          " (안 맞은 만큼 그 보정은 적용되지 않음)")
    if rep.held_back:
        print(f"  분류가 안 정해져 이번 CSV 에서 뺀 새 제품 {len(rep.held_back):,}개 → pipeline categorize export")
    return 0


def cmd_seed(s: Settings, a) -> int:
    from . import build
    conn = db.connect(s.state_db)
    try:
        if a.what == "categories":
            n, with_cat = build.seed_categories(conn, Path(a.file), replace=a.replace)
            print(f"분류표 {n:,}행 (분류 있음 {with_cat:,} · 분류 제외 {n - with_cat:,})")
        else:
            n, groups = build.seed_groups(conn, Path(a.file), replace=a.replace)
            print(f"묶음 구성 {n:,}행 · 묶음 {groups:,}개")
    except build.BuildRefused as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_categorize(s: Settings, a) -> int:
    from . import build
    conn = db.connect(s.state_db)
    path = Path(a.file)
    if a.action == "export":
        n = build.export_review(conn, path)
        print(f"검토 대기 {n:,}개 → {path}")
        print(f"  category 칸을 채운 뒤 pipeline categorize import {path} (분류 제외로 확정하려면 「{build.NO_CATEGORY}」)")
    else:
        done, skipped = build.import_review(conn, path)
        print(f"분류표에 {done:,}개 넣음 · category 가 비어 건너뜀 {skipped:,}개")
    return 0


def cmd_diff(_s: Settings, a) -> int:
    from . import build
    rep = build.diff(Path(a.old), Path(a.new), a.examples)
    print(f"이전 {rep.old_rows:,}행 / 새 {rep.new_rows:,}행")
    print(f"  같음 {rep.same:,} · 바뀜 {rep.changed:,} · 새로 생김 {len(rep.added):,} · 사라짐 {len(rep.removed):,}")
    for col, n in sorted(rep.by_column.items(), key=lambda kv: -kv[1]):
        print(f"  [{col}] {n:,}행")
        for mid, old, new in rep.examples[col]:
            print(f"      {mid}: {old!r} → {new!r}")
    for title, ids in (("새로 생김", rep.added), ("사라짐", rep.removed)):
        if ids:
            print(f"  {title} 예: {', '.join(ids[:a.examples])}")
    return 0


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
    b.add_argument("--force", action="store_true", help="새로 끝난 회차가 없어도 만듦")
    b.add_argument("--dry-run", action="store_true", help="계산만 하고 data/preview/ 에 씀 (api 가 읽는 csv/ 와 state.db 는 안 건드림)")
    b.set_defaults(fn=cmd_build, locked=True)

    sd = sub.add_parser("seed")
    sd.add_argument("what", choices=["categories", "groups"])
    sd.add_argument("file")
    sd.add_argument("--replace", action="store_true", help="이미 있으면 지우고 다시 채움")
    sd.set_defaults(fn=cmd_seed, locked=True)

    c = sub.add_parser("categorize")
    c.add_argument("action", choices=["export", "import"])
    c.add_argument("file", nargs="?", default="category_review.csv")
    c.set_defaults(fn=cmd_categorize, locked=True)

    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--examples", type=int, default=5)
    d.set_defaults(fn=cmd_diff, locked=False)

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