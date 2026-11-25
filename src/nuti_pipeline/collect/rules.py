"""수집 규칙 — 순수 함수 3개 (Java CallBudget · RoundProgress · CollectSchedule). 테스트로 고정.

시각은 전부 KST, tzinfo 없는 datetime 으로 다룬다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from ..apis import Api, Condition, Provider

# ── 호출 한도 (CallBudget) ────────────────────────────────────────────────
# - 하루 한도의 90% 까지만 (재시도 · 손으로 확인할 몫을 남김)
# - 식품안전나라 낮(06~19시)은 서비스별 300회 → 270회
# - 식품안전나라를 CHNG_DT 없이 부르면(전량 · I0490 · EVL_YR) 1시간 100회 → 90회
# - 한도는 API 별로 따로 센다 (C005 가 막혀도 C002 는 된다)

SAFETY = 0.9
FOOD_SAFETY_DAYTIME_LIMIT = 300
FOOD_SAFETY_HOURLY_LIMIT_WITHOUT_CHNG_DT = 100
DAYTIME_FROM, DAYTIME_TO = 6, 19


@dataclass(frozen=True)
class Used:
    today: int          # 오늘 0시부터
    last_hour: int      # 최근 1시간
    daytime_today: int  # 오늘 06~19시


def allowed_calls(api: Api, daily_limit: int, using_chng_dt: bool, now: datetime, used: Used, wanted: int) -> int:
    left = math.floor(daily_limit * SAFETY) - used.today
    if api.provider is Provider.FOOD_SAFETY:
        if DAYTIME_FROM <= now.hour < DAYTIME_TO:
            left = min(left, math.floor(FOOD_SAFETY_DAYTIME_LIMIT * SAFETY) - used.daytime_today)
        if not using_chng_dt:
            left = min(left, math.floor(FOOD_SAFETY_HOURLY_LIMIT_WITHOUT_CHNG_DT * SAFETY) - used.last_hour)
    return max(0, min(left, wanted))


# ── 회차 끝 판정 (RoundProgress) ──────────────────────────────────────────
# - 받은 행 수 = totalCount 면 끝. 넘거나, 모자란 채 빈/덜 찬 페이지가 오면 실패
# - C005 · totalCount 없음 → 빈/덜 찬 페이지에서 끝

class Next(str, Enum):
    CONTINUE = "CONTINUE"
    DONE = "DONE"
    COUNT_MISMATCH = "COUNT_MISMATCH"


def after_page(api: Api, total: int | None, received_after: int, rows_this_page: int) -> Next:
    last_page = rows_this_page < api.page_size  # 빈 페이지 포함
    if api.total_untrusted or total is None:
        return Next.DONE if last_page else Next.CONTINUE
    if received_after == total:
        return Next.DONE
    if received_after > total:
        return Next.COUNT_MISMATCH
    return Next.COUNT_MISMATCH if last_page else Next.CONTINUE


# ── 주기 (CollectSchedule) ────────────────────────────────────────────────
# | C002 · C006 · I2852 · C005 · I2570 | 매일 19시 이후   | 증분. CHNG_DT = 마지막으로 끝까지 받은 회차의 시작일 |
# | 같은 5개                           | 30일마다, 밤     | 전량. 마지막 성공이 7일보다 오래되면 이걸로 복구      |
# | I0490                              | 매일             | 전량                                                 |
# | 영양성분DB · 푸드QR 3종            | 7일마다          | 전량                                                 |
# | HACCP                              | 30일마다         | 전량                                                 |
# | I0300                              | 해마다, 밤       | 전량, EVL_YR = 작년                                  |
# 받는 중인 회차가 있으면 열지 않음. 전량이 실패하면 24시간 뒤 다시 엶.
# 식품안전나라 큰 전량(I0490 제외)은 밤(19~06시)에만 이어 받음.

NIGHT_FROM, NIGHT_TO = 19, 6
CHNG_DT_FULL_DAYS, CHNG_DT_MAX_DAYS = 30, 7
DATAGO_WEEKLY_DAYS, HACCP_DAYS = 7, 30


class Mode(str, Enum):
    FULL = "FULL"
    INCREMENTAL = "INCREMENTAL"


class RoundStatus(str, Enum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RoundInfo:
    mode: Mode
    status: RoundStatus
    cond_value: str | None
    started_at: datetime


@dataclass(frozen=True)
class Snapshot:
    latest: RoundInfo | None = None          # 마지막 회차 (무엇이든)
    latest_full: RoundInfo | None = None     # 마지막 전량 회차 (무엇이든)
    last_done: RoundInfo | None = None       # 마지막으로 끝까지 받은 회차 (전량 · 증분)
    last_full_done: RoundInfo | None = None  # 마지막으로 끝까지 받은 전량 회차


@dataclass(frozen=True)
class Start:
    mode: Mode
    condition: str | None  # CHNG_DT=YYYYMMDD / EVL_YR=YYYY / None


def is_night(now: datetime) -> bool:
    return now.hour >= NIGHT_FROM or now.hour < NIGHT_TO


def _full_due(s: Snapshot, now: datetime, days: int) -> bool:
    return s.last_full_done is None or s.last_full_done.started_at < now - timedelta(days=days)


def _recent_full_failure(s: Snapshot, now: datetime) -> bool:
    f = s.latest_full
    return f is not None and f.status is RoundStatus.FAILED and f.started_at > now - timedelta(days=1)


def decide(api: Api, now: datetime, s: Snapshot) -> Start | None:
    if s.latest is not None and s.latest.status is RoundStatus.RUNNING:
        return None
    night = is_night(now)
    today = now.date()

    if api.condition is Condition.CHNG_DT:
        if _full_due(s, now, CHNG_DT_FULL_DAYS) and night and not _recent_full_failure(s, now):
            return Start(Mode.FULL, None)
        if now.hour < NIGHT_FROM:  # 오늘 바뀐 자료는 19시 이후에 나온다
            return None
        if s.latest is not None and s.latest.started_at.date() == today:
            return None
        if s.last_done is None:  # 전량을 기다린다
            return None
        since: date = s.last_done.started_at.date()
        if since < today - timedelta(days=CHNG_DT_MAX_DAYS):  # 증분 불가 — 전량을 기다린다
            return None
        return Start(Mode.INCREMENTAL, since.strftime("%Y%m%d"))

    if api.condition is Condition.EVL_YR:
        target = str(today.year - 1)
        done = s.last_full_done is not None and s.last_full_done.cond_value == target
        if not done and night and not _recent_full_failure(s, now):
            return Start(Mode.FULL, target)
        return None

    if api.name == "I0490":
        started_today = s.latest is not None and s.latest.started_at.date() == today
        return None if started_today else Start(Mode.FULL, None)

    days = HACCP_DAYS if api.name == "HACCP" else DATAGO_WEEKLY_DAYS
    if _full_due(s, now, days) and not _recent_full_failure(s, now):
        return Start(Mode.FULL, None)
    return None


def step_allowed(api: Api, mode: Mode, now: datetime) -> bool:
    """이 회차를 지금 받아도 되는가 — 식품안전나라 큰 전량은 밤에만."""
    big_food_safety_full = api.provider is Provider.FOOD_SAFETY and mode is Mode.FULL and api.name != "I0490"
    return not big_food_safety_full or is_night(now)