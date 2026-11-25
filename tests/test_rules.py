"""CallBudget · RoundProgress · CollectScheduleTest 이식."""
from __future__ import annotations

from datetime import datetime

from nuti_pipeline.apis import APIS
from nuti_pipeline.collect.rules import (Mode, Next, RoundInfo, RoundStatus, Snapshot, Start, Used, after_page,
                                         allowed_calls, decide, step_allowed)

A = APIS
EVENING = datetime(2026, 11, 17, 19, 30)
NOON = datetime(2026, 11, 17, 12, 0)
LATE_NIGHT = datetime(2026, 11, 17, 2, 0)


# ── 호출 한도 ──

def test_하루_한도의_90퍼센트까지만():
    assert allowed_calls(A["NUTRI_DB"], 1000, False, NOON, Used(0, 0, 0), 2000) == 900
    assert allowed_calls(A["NUTRI_DB"], 1000, False, NOON, Used(890, 0, 0), 100) == 10
    assert allowed_calls(A["NUTRI_DB"], 1000, False, NOON, Used(950, 0, 0), 100) == 0


def test_식품안전나라_낮에는_270회():
    assert allowed_calls(A["C002"], 1000, True, NOON, Used(0, 0, 0), 1000) == 270
    assert allowed_calls(A["C002"], 1000, True, NOON, Used(300, 0, 260), 100) == 10
    assert allowed_calls(A["C002"], 1000, True, EVENING, Used(300, 0, 300), 1000) == 600  # 19시 이후는 낮 제한 없음


def test_식품안전나라_조건_없이_부르면_1시간_90회():
    assert allowed_calls(A["I0490"], 2000, False, LATE_NIGHT, Used(0, 0, 0), 1000) == 90
    assert allowed_calls(A["I0490"], 2000, False, LATE_NIGHT, Used(100, 85, 0), 1000) == 5
    assert allowed_calls(A["C002"], 1000, True, LATE_NIGHT, Used(0, 85, 0), 100) == 100  # 증분은 1시간 제한 없음


def test_공공데이터포털에는_낮_시간_제한이_없다():
    assert allowed_calls(A["HACCP"], 1000, False, NOON, Used(0, 500, 500), 1000) == 900


# ── 회차 끝 판정 ──

def test_받은_행_수가_전체와_같으면_끝():
    assert after_page(A["C002"], 2500, 1000, 1000) is Next.CONTINUE
    assert after_page(A["C002"], 2500, 2500, 500) is Next.DONE
    assert after_page(A["C002"], 2000, 2000, 1000) is Next.DONE


def test_모자란_채_덜_찬_페이지거나_넘치면_불일치():
    assert after_page(A["C002"], 2500, 2400, 400) is Next.COUNT_MISMATCH
    assert after_page(A["C002"], 2500, 2000, 0) is Next.COUNT_MISMATCH
    assert after_page(A["C002"], 2500, 2600, 1000) is Next.COUNT_MISMATCH


def test_C005와_전체_수_없음은_덜_찬_페이지에서_끝():
    assert after_page(A["C005"], 999999, 1000, 1000) is Next.CONTINUE
    assert after_page(A["C005"], 999999, 1500, 500) is Next.DONE
    assert after_page(A["C005"], 2000, 2000, 0) is Next.DONE
    assert after_page(A["NUTRI_DB"], None, 500, 500) is Next.CONTINUE
    assert after_page(A["NUTRI_DB"], None, 700, 200) is Next.DONE


# ── 주기 ──

def _done(mode: Mode, at: datetime, cond: str | None = None) -> RoundInfo:
    return RoundInfo(mode, RoundStatus.DONE, cond, at)


def _full_done_at(at: datetime) -> Snapshot:
    f = _done(Mode.FULL, at)
    return Snapshot(f, f, f, f)


def test_처음이면_밤에_전량():
    assert decide(A["C002"], EVENING, Snapshot()) == Start(Mode.FULL, None)
    assert decide(A["C002"], NOON, Snapshot()) is None


def test_매일_19시_이후_증분_CHNG_DT는_마지막_성공_시작일():
    s = _full_done_at(datetime(2026, 11, 14, 20, 0))
    assert decide(A["C005"], EVENING, s) == Start(Mode.INCREMENTAL, "20261114")
    assert decide(A["C005"], NOON, s) is None


def test_오늘_이미_연_회차가_있으면_증분을_다시_열지_않는다():
    full = _done(Mode.FULL, datetime(2026, 11, 10, 20, 0))
    today_failed = RoundInfo(Mode.INCREMENTAL, RoundStatus.FAILED, "20261110", datetime(2026, 11, 17, 19, 10))
    assert decide(A["C002"], EVENING, Snapshot(today_failed, full, full, full)) is None


def test_마지막_성공이_7일보다_오래되면_증분_대신_전량을_기다린다():
    full = _done(Mode.FULL, datetime(2026, 11, 1, 20, 0))
    inc = _done(Mode.INCREMENTAL, datetime(2026, 11, 9, 19, 30))
    assert decide(A["I2852"], EVENING, Snapshot(inc, full, inc, full)) is None


def test_전량이_30일_지나면_밤에_전량():
    assert decide(A["C006"], EVENING, _full_done_at(datetime(2026, 10, 15, 20, 0))) == Start(Mode.FULL, None)


def test_전량이_실패하면_24시간_뒤에_다시():
    failed = RoundInfo(Mode.FULL, RoundStatus.FAILED, None, datetime(2026, 11, 17, 1, 0))
    s = Snapshot(failed, failed, None, None)
    assert decide(A["C002"], LATE_NIGHT, s) is None
    assert decide(A["C002"], datetime(2026, 11, 18, 2, 0), s) == Start(Mode.FULL, None)


def test_받는_중이면_열지_않는다():
    running = RoundInfo(Mode.FULL, RoundStatus.RUNNING, None, datetime(2026, 11, 16, 20, 0))
    s = Snapshot(running, running, None, None)
    for api in A.values():
        assert decide(api, EVENING, s) is None


def test_I0490은_하루_한_번_전량():
    assert decide(A["I0490"], NOON, Snapshot()) == Start(Mode.FULL, None)
    assert decide(A["I0490"], NOON, _full_done_at(datetime(2026, 11, 17, 0, 10))) is None


def test_영양성분DB_푸드QR은_7일_HACCP은_30일마다_전량_낮에도():
    eight = _full_done_at(datetime(2026, 11, 9, 11, 0))
    assert decide(A["NUTRI_DB"], NOON, eight) == Start(Mode.FULL, None)
    assert decide(A["FOODQR_NUTRITION"], NOON, eight) == Start(Mode.FULL, None)
    assert decide(A["HACCP"], NOON, eight) is None
    assert decide(A["NUTRI_DB"], NOON, _full_done_at(datetime(2026, 11, 12, 11, 0))) is None


def test_생산실적은_해마다_작년_것을_밤에():
    assert decide(A["I0300"], EVENING, Snapshot()) == Start(Mode.FULL, "2025")
    assert decide(A["I0300"], NOON, Snapshot()) is None
    y = _done(Mode.FULL, datetime(2026, 1, 3, 20, 0), "2025")
    assert decide(A["I0300"], EVENING, Snapshot(y, y, y, y)) is None


def test_식품안전나라_큰_전량은_밤에만_받는다():
    assert not step_allowed(A["C002"], Mode.FULL, NOON)
    assert step_allowed(A["C002"], Mode.FULL, LATE_NIGHT)
    assert step_allowed(A["C002"], Mode.INCREMENTAL, NOON)
    assert step_allowed(A["I0490"], Mode.FULL, NOON)
    assert step_allowed(A["NUTRI_DB"], Mode.FULL, NOON)