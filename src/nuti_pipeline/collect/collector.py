"""수집기 (Java PublicApiCollector + PublicDataRunner.tick) — 응답을 state.db 의 raw 표에 그대로 쌓는다.

- 회차 단위로 받는다. start 로 열고, step 으로 한도 안에서 몇 페이지씩 받는다.
  한도에 걸리면 멈췄다가 다음 실행(cron 10분 뒤)에서 next_page 부터 이어 받는다.
- 페이지마다 따로 커밋 → 중간에 꺼져도 받은 페이지까지는 남는다.
- 끝 판정은 rules.after_page. 행 수가 맞지 않으면 실패로 돌리고 그 회차 행을 지운다.
- 증분인데 전체 행 수가 10만을 넘으면 조건이 무시된 것으로 보고 실패.
- 전량인데 0행으로 끝나면 실패 (그대로 끝내면 이전 원본을 다 지우게 되므로).
- 전량 회차가 끝나면 그 회차보다 먼저 시작한 회차의 행을 지운다 (원천에서 삭제된 줄을 걸러내는 유일한 방법).
- 계산(build)은 DONE 회차의 행만 읽는다 → 받는 중인 회차는 계산에 섞이지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..apis import APIS, Api, Condition, Provider
from ..config import KST
from . import rules
from .clients import Client, Page, Status
from .rules import Mode, RoundInfo, RoundStatus, Snapshot, Used

log = logging.getLogger(__name__)

MAX_INCREMENTAL_TOTAL = 100_000   # 증분인데 이보다 많으면 조건이 무시된 것
MAX_CALLS_PER_STEP = 100          # 한 번에 한 회차를 이어 받는 최대 호출 수 (실제로는 한도가 더 줄임)
CALL_LOG_KEEP_DAYS = 30

# 원본이 큰 API 는 34컬럼 계산에 쓰는 필드만 저장한다 (디스크 절약). 나머지 API 는 응답 행 전체를 저장.
# 여기서 빠진 필드가 나중에 필요해지면 그 API 를 전량으로 다시 받아야 한다.
KEEP_FIELDS: dict[str, tuple[str, ...]] = {
    # 약 35만 행, 한 행에 영양성분 칸이 157개라 통째로 두면 1GB 이상. 34컬럼 · 브랜드 · 분류에 쓰는 칸만 남긴다
    "NUTRI_DB": (
        "FOOD_CD", "FOOD_NM_KR", "DB_GRP_NM", "DB_CLASS_NM",
        "FOOD_CAT1_NM", "FOOD_REF_CD", "FOOD_REF_NM",              # 분류
        "MAKER_NM", "SELLER_MANUFAC_NM", "IMP_MANUFAC_NM",         # 제조사 · 브랜드
        "ITEM_REPORT_NO", "Z10500", "SERVING_SIZE", "NUTRI_AMOUNT_SERVING", "UPDATE_DATE",
        # 열량 · 나트륨 · 탄수화물 · 당류 · 지방 · 트랜스지방 · 포화지방 · 콜레스테롤 · 식이섬유 · 단백질
        "AMT_NUM1", "AMT_NUM13", "AMT_NUM6", "AMT_NUM7", "AMT_NUM4",
        "AMT_NUM25", "AMT_NUM24", "AMT_NUM23", "AMT_NUM8", "AMT_NUM3",
    ),
    "C002":("PRDLST_REPORT_NO", "RAWMTRL_NM", "CHNG_DT"),   # 약 108만 행
    "C006": ("PRDLST_REPORT_NO", "RAWMTRL_NM", "CHNG_DT"),
    "I2852": ("PRDLST_REPORT_NO", "CHNG_DT"),                # 약 188만 행
    "I0300": ("SITE_ADDR",),                                 # 지명 목록에만 쓴다
}

TS = "%Y-%m-%dT%H:%M:%S"


def kst_now() -> datetime:
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


def _ts(d: datetime) -> str:
    return d.strftime(TS)


class CollectError(ValueError):
    """회차를 열거나 이어 받을 수 없는 경우 (조건 값 오류, 이미 받는 중 등)."""


@dataclass
class TickResult:
    started: list[str] = field(default_factory=list)
    stepped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_condition(api: Api, mode: Mode, condition: str | None, today: date) -> str | None:
    """조건 값 검사 — 저장할 값을 돌려준다."""
    c = (condition or "").strip()
    if mode is Mode.INCREMENTAL:
        if api.condition is not Condition.CHNG_DT:
            raise CollectError(f"{api.name} 은 증분(CHNG_DT)을 지원하지 않습니다. 전량으로 받으세요.")
        try:
            d = datetime.strptime(c, "%Y%m%d").date()
        except ValueError:
            raise CollectError(f"증분 조건은 YYYYMMDD 입니다: {c!r}") from None
        if d > today or d < today - timedelta(days=rules.CHNG_DT_MAX_DAYS):
            raise CollectError(f"CHNG_DT 는 오늘부터 {rules.CHNG_DT_MAX_DAYS}일 전까지만 됩니다. 더 오래 멈췄으면 전량으로 받으세요.")
        return c
    if api.condition is Condition.EVL_YR:
        if not (len(c) == 4 and c.isdigit()) or int(c) > today.year:
            raise CollectError(f"{api.name} 은 연도(EVL_YR, YYYY)가 필요합니다: {c!r}")
        return c
    return None


class Collector:
    def __init__(self, conn: sqlite3.Connection, clients: dict[Provider, Client], datagokr_daily_limit: int,
                 now: Callable[[], datetime] = kst_now) -> None:
        self.conn = conn
        self.clients = clients
        self.datagokr_daily_limit = datagokr_daily_limit
        self.now = now

    # ── 회차 열기 ─────────────────────────────────────────────────────────

    def start(self, api_name: str, mode: Mode, condition: str | None = None) -> int:
        api = APIS[api_name]
        now = self.now()
        value = validate_condition(api, mode, condition, now.date())
        running = self.conn.execute("SELECT id FROM rounds WHERE api=? AND status='RUNNING'", (api.name,)).fetchone()
        if running:
            raise CollectError(f"{api.name} 은 이미 받는 중인 회차가 있습니다(회차 {running['id']}). 이어 받거나 취소한 뒤 여세요.")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO rounds(api, mode, cond_value, status, started_at) VALUES (?,?,?,'RUNNING',?)",
                (api.name, mode.value, value, _ts(now)))
        log.info("%s %s 회차 시작 id=%d 조건=%s", api.name, mode.value, cur.lastrowid, value)
        return cur.lastrowid

    # ── 이어 받기 ─────────────────────────────────────────────────────────

    def step(self, round_id: int, max_calls: int = MAX_CALLS_PER_STEP) -> sqlite3.Row:
        r = self._round(round_id)
        if r["status"] != RoundStatus.RUNNING.value:
            raise CollectError(f"이미 끝난 회차입니다({r['status']}).")
        api = APIS[r["api"]]
        mode = Mode(r["mode"])
        client = self.clients[api.provider]

        allowed = rules.allowed_calls(api, self.daily_limit(api), mode is Mode.INCREMENTAL,
                                      self.now(), self._used(api), max_calls)
        if allowed == 0:
            log.info("%s 회차 %d — 지금은 호출 한도가 남지 않았습니다", api.name, round_id)
        for _ in range(allowed):
            r = self._round(round_id)
            page = client.fetch(api, r["next_page"], r["cond_value"])
            with self.conn:
                self.conn.execute("INSERT INTO call_log(api, called_at, code) VALUES (?,?,?)",
                                  (api.name, _ts(self.now()), page.code))
            if not self._handle(r, api, mode, page):
                break
        return self._round(round_id)

    def _handle(self, r: sqlite3.Row, api: Api, mode: Mode, page: Page) -> bool:
        """한 페이지 결과 처리. 다음 페이지를 계속 불러도 되면 True."""
        rid = r["id"]
        if page.status in (Status.OK, Status.NO_DATA):
            if (mode is Mode.INCREMENTAL and r["next_page"] == 1
                    and page.total_count is not None and page.total_count > MAX_INCREMENTAL_TOTAL):
                self._fail(rid, "CONDITION_IGNORED", f"증분인데 전체 {page.total_count}행 — 조건이 무시된 것으로 봅니다")
                return False

            received = r["rows_received"] + len(page.rows)
            total = page.total_count if page.total_count is not None else r["total_count"]
            nxt = rules.after_page(api, total, received, len(page.rows))
            now = _ts(self.now())
            with self.conn:  # 페이지 하나 = 트랜잭션 하나
                self._insert_rows(api, rid, page.rows)
                self.conn.execute(
                    "UPDATE rounds SET next_page=next_page+1, rows_received=?, total_count=?, last_code=?, last_message=? "
                    "WHERE id=?", (received, total, page.code, page.message, rid))
                if nxt is rules.Next.DONE:
                    if mode is Mode.FULL and received == 0:
                        self._mark_failed(rid, "EMPTY_FULL", "전량 회차인데 받은 행이 0개 — 이전 원본을 지우지 않습니다", now)
                        log.warning("%s 전량 회차 %d 0행으로 실패", api.name, rid)
                        return False
                    self.conn.execute("UPDATE rounds SET status='DONE', finished_at=?, last_message=? WHERE id=?",
                                      (now, f"받은 {received}행", rid))
                    if mode is Mode.FULL:
                        removed = self.conn.execute(
                            "DELETE FROM raw WHERE api=? AND round_id IN "
                            "(SELECT id FROM rounds WHERE api=? AND id<>? AND started_at<?)",
                            (api.name, api.name, rid, r["started_at"])).rowcount
                        log.info("%s 전량 회차 %d 끝 — %d행, 이전 행 %d개 지움", api.name, rid, received, removed)
                    else:
                        log.info("%s 증분 회차 %d 끝 — %d행", api.name, rid, received)
                elif nxt is rules.Next.COUNT_MISMATCH:
                    self._mark_failed(rid, "COUNT_MISMATCH", f"받은 {received}행 / 전체 {total}행", now)
                    log.warning("%s 회차 %d 행 수 불일치로 실패 — 받은 %d / 전체 %s", api.name, rid, received, total)
            return nxt is rules.Next.CONTINUE

        if page.status in (Status.QUOTA_EXCEEDED, Status.RETRY_LATER):
            with self.conn:
                self.conn.execute("UPDATE rounds SET last_code=?, last_message=? WHERE id=?",
                                  (page.code, page.message, rid))
            log.info("%s 회차 %d 멈춤 (%s %s) — 다음에 %d페이지부터 이어 받습니다",
                     api.name, rid, page.code, page.message, r["next_page"])
            return False

        self._fail(rid, page.code, page.message)  # KEY_REJECTED, ERROR
        return False

    def _insert_rows(self, api: Api, round_id: int, rows: list[dict]) -> None:
        keep = KEEP_FIELDS.get(api.name)
        batch = []
        for row in rows:
            if keep:
                row = {k: row.get(k) for k in keep}
            data = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            batch.append((api.name, round_id, hashlib.sha256(data.encode("utf-8")).hexdigest(), data))
        self.conn.executemany("INSERT OR IGNORE INTO raw(api, round_id, row_hash, data) VALUES (?,?,?,?)", batch)

    def _mark_failed(self, rid: int, code: str, message: str, now: str) -> None:
        """호출한 쪽 트랜잭션 안에서 — 회차를 실패로 돌리고 받은 행을 지운다 (일부만 받은 채 계산하지 않게)."""
        self.conn.execute("UPDATE rounds SET status='FAILED', finished_at=?, last_code=?, last_message=? WHERE id=?",
                          (now, code, message, rid))
        self.conn.execute("DELETE FROM raw WHERE round_id=?", (rid,))

    def _fail(self, rid: int, code: str, message: str) -> None:
        with self.conn:
            self._mark_failed(rid, code, message, _ts(self.now()))
        log.warning("회차 %d 실패 — %s %s", rid, code, message)

    def cancel(self, round_id: int) -> None:
        r = self._round(round_id)
        if r["status"] != RoundStatus.RUNNING.value:
            raise CollectError(f"이미 끝난 회차입니다({r['status']}).")
        self._fail(round_id, "CANCELLED", "수동 취소")

    # ── 한 바퀴 (cron 10분마다) ───────────────────────────────────────────

    def tick(self) -> TickResult:
        res = TickResult()
        now = self.now()
        for api in APIS.values():
            try:
                plan = rules.decide(api, now, self.snapshot(api.name))
                if plan:
                    rid = self.start(api.name, plan.mode, plan.condition)
                    res.started.append(f"{api.name} {plan.mode.value} 회차 {rid} (조건 {plan.condition})")
            except CollectError as e:
                res.errors.append(f"{api.name} 회차 열기 실패: {e}")
                log.warning("%s 회차 열기 실패 — %s", api.name, e)

        running = self.conn.execute("SELECT id, api, mode FROM rounds WHERE status='RUNNING' ORDER BY id").fetchall()
        for row in running:
            api = APIS[row["api"]]
            if not rules.step_allowed(api, Mode(row["mode"]), self.now()):
                res.skipped.append(f"{api.name} 전량(회차 {row['id']})은 밤(19~06시)에만 받습니다")
                continue
            try:
                r = self.step(row["id"])
                res.stepped.append(f"{api.name} 회차 {r['id']}: {r['status']} {r['rows_received']}행"
                                   f"/{r['total_count']} 다음 {r['next_page']}페이지 ({r['last_code']})")
            except CollectError as e:
                res.errors.append(f"{api.name} 이어 받기 실패: {e}")

        self.prune_call_logs()
        return res

    # ── 조회 ──────────────────────────────────────────────────────────────

    def snapshot(self, api_name: str) -> Snapshot:
        def one(sql: str, *args) -> RoundInfo | None:
            row = self.conn.execute(
                f"SELECT mode, status, cond_value, started_at FROM rounds WHERE api=? {sql} ORDER BY id DESC LIMIT 1",
                (api_name, *args)).fetchone()
            if not row:
                return None
            return RoundInfo(Mode(row["mode"]), RoundStatus(row["status"]), row["cond_value"],
                             datetime.strptime(row["started_at"], TS))
        return Snapshot(
            latest=one(""),
            latest_full=one("AND mode='FULL'"),
            last_done=one("AND status='DONE'"),
            last_full_done=one("AND mode='FULL' AND status='DONE'"),
        )

    def daily_limit(self, api: Api) -> int:
        return self.datagokr_daily_limit if api.daily_limit is None else api.daily_limit

    def _used(self, api: Api) -> Used:
        now = self.now()
        end = _ts(now + timedelta(seconds=1))
        day0 = datetime.combine(now.date(), datetime.min.time())

        def count(frm: datetime, to: str) -> int:
            return self.conn.execute("SELECT COUNT(*) FROM call_log WHERE api=? AND called_at>=? AND called_at<?",
                                     (api.name, _ts(frm), to)).fetchone()[0]
        return Used(
            today=count(day0, end),
            last_hour=count(now - timedelta(hours=1), end),
            daytime_today=count(day0.replace(hour=rules.DAYTIME_FROM), _ts(day0.replace(hour=rules.DAYTIME_TO))),
        )

    def calls_today(self, api: Api) -> int:
        return self._used(api).today

    def prune_call_logs(self) -> int:
        with self.conn:
            n = self.conn.execute("DELETE FROM call_log WHERE called_at<?",
                                  (_ts(self.now() - timedelta(days=CALL_LOG_KEEP_DAYS)),)).rowcount
        return n

    def _round(self, round_id: int) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone()
        if not r:
            raise CollectError(f"없는 회차입니다: {round_id}")
        return r