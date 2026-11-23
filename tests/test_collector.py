"""수집기 — 가짜 API 로 회차 흐름 확인 (state.db 는 임시 폴더)."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from nuti_pipeline.apis import APIS, Provider
from nuti_pipeline.collect.clients import Page, Status
from nuti_pipeline.collect.collector import Collector, CollectError
from nuti_pipeline.collect.rules import Mode
from nuti_pipeline.state import db

NIGHT = datetime(2026, 11, 23, 22, 0)


class FakeClient:
    """페이지 번호 → Page. 없는 페이지는 빈 페이지."""

    def __init__(self, pages: dict[int, Page] | None = None):
        self.pages = pages or {}
        self.calls: list[tuple[str, int, str | None]] = []

    def fetch(self, api, page, cond):
        self.calls.append((api.name, page, cond))
        return self.pages.get(page, Page(Status.OK, "INFO-000", "", 0, []))


def rows(prefix: str, n: int) -> list[dict]:
    return [{"PRDLST_REPORT_NO": f"{prefix}{i}", "RAWMTRL_NM": "물", "CHNG_DT": "20261120", "EXTRA": "x"} for i in range(n)]


def ok(rs, total):
    return Page(Status.OK, "INFO-000", "정상", total, rs)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "state.db")
    yield c
    c.close()


def make(conn, client, now=NIGHT):
    return Collector(conn, {Provider.FOOD_SAFETY: client, Provider.DATA_GO_KR: client}, 1000, now=lambda: now)


def raw_count(conn, api=None, rid=None):
    sql, args = "SELECT COUNT(*) FROM raw WHERE 1=1", []
    if api:
        sql += " AND api=?"; args.append(api)
    if rid:
        sql += " AND round_id=?"; args.append(rid)
    return conn.execute(sql, args).fetchone()[0]


def test_전량_회차를_끝까지_받고_큰_API는_필요한_필드만_저장(conn):
    client = FakeClient({1: ok(rows("A", 1000), 1500), 2: ok(rows("B", 500), 1500)})
    c = make(conn, client)
    rid = c.start("C002", Mode.FULL)
    r = c.step(rid)
    assert r["status"] == "DONE" and r["rows_received"] == 1500
    assert len(client.calls) == 2
    data = json.loads(conn.execute("SELECT data FROM raw LIMIT 1").fetchone()[0])
    assert set(data) == {"PRDLST_REPORT_NO", "RAWMTRL_NM", "CHNG_DT"}  # EXTRA 는 버림


def test_전량이_끝나면_이전_회차_행을_지운다(conn):
    c = make(conn, FakeClient({1: ok(rows("A", 10), 10)}))
    old = c.start("I0490", Mode.FULL)
    c.step(old)
    assert raw_count(conn, rid=old) == 10
    c.clients[Provider.FOOD_SAFETY] = FakeClient({1: ok(rows("B", 7), 7)})
    conn.execute("UPDATE rounds SET started_at='2026-11-22T22:00:00' WHERE id=?", (old,)); conn.commit()
    new = c.start("I0490", Mode.FULL)
    c.step(new)
    assert raw_count(conn, rid=old) == 0
    assert raw_count(conn, rid=new) == 7


def test_행_수가_모자라면_실패하고_받은_행을_지운다(conn):
    c = make(conn, FakeClient({1: ok(rows("A", 1000), 2500), 2: ok(rows("B", 300), 2500)}))
    rid = c.start("C002", Mode.FULL)
    r = c.step(rid)
    assert r["status"] == "FAILED" and r["last_code"] == "COUNT_MISMATCH"
    assert raw_count(conn, rid=rid) == 0


def test_전량인데_0행이면_실패(conn):
    c = make(conn, FakeClient({1: Page(Status.NO_DATA, "INFO-200", "없음", 0, [])}))
    rid = c.start("I0490", Mode.FULL)
    r = c.step(rid)
    assert r["status"] == "FAILED" and r["last_code"] == "EMPTY_FULL"


def test_한도_초과면_멈추고_다음에_그_페이지부터_이어_받는다(conn):
    client = FakeClient({1: ok(rows("A", 1000), 2000), 2: Page(Status.QUOTA_EXCEEDED, "INFO-300", "한도")})
    c = make(conn, client)
    rid = c.start("C002", Mode.FULL)
    r = c.step(rid)
    assert r["status"] == "RUNNING" and r["next_page"] == 2 and r["rows_received"] == 1000
    client.pages[2] = ok(rows("B", 1000), 2000)
    r = c.step(rid)
    assert r["status"] == "DONE" and r["rows_received"] == 2000
    assert [p for _, p, _ in client.calls] == [1, 2, 2]


def test_키_거부는_실패(conn):
    c = make(conn, FakeClient({1: Page(Status.KEY_REJECTED, "HTML", "키")}))
    rid = c.start("C005", Mode.FULL)
    assert c.step(rid)["status"] == "FAILED"


def test_증분인데_전체가_10만을_넘으면_조건_무시로_실패(conn):
    c = make(conn, FakeClient({1: ok(rows("A", 1000), 1_081_400)}))
    rid = c.start("C002", Mode.INCREMENTAL, "20261120")
    r = c.step(rid)
    assert r["status"] == "FAILED" and r["last_code"] == "CONDITION_IGNORED"


def test_증분_조건_검사(conn):
    c = make(conn, FakeClient())
    with pytest.raises(CollectError):
        c.start("C002", Mode.INCREMENTAL, "20261101")  # 7일보다 오래됨
    with pytest.raises(CollectError):
        c.start("I0490", Mode.INCREMENTAL, "20261120")  # 증분 미지원
    with pytest.raises(CollectError):
        c.start("I0300", Mode.FULL, "25")


def test_받는_중인_회차가_있으면_새로_못_연다(conn):
    c = make(conn, FakeClient())
    c.start("C002", Mode.FULL)
    with pytest.raises(CollectError):
        c.start("C002", Mode.FULL)


def test_호출_한도를_넘지_않는다(conn):
    client = FakeClient({p: ok(rows(f"P{p}-", 1000), 10_000_000) for p in range(1, 200)})
    c = make(conn, client, now=datetime(2026, 11, 23, 12, 0))  # 낮: 식품안전나라 270회, 증분 아님 → 1시간 90회
    rid = c.start("C002", Mode.FULL)
    c.step(rid, max_calls=500)
    assert len(client.calls) == 90
    c.step(rid, max_calls=500)
    assert len(client.calls) == 90  # 같은 시각엔 더 못 부름


def test_tick은_주기가_된_회차를_열고_이어_받는다(conn):
    client = FakeClient({1: ok(rows("A", 3), 3)})
    c = make(conn, client)
    res = c.tick()
    opened = {s.split()[0] for s in res.started}
    assert opened == set(APIS)  # 처음 + 밤 → 13개 전부 엶
    assert not res.errors
    statuses = {r["api"]: r["status"] for r in conn.execute("SELECT api, status FROM rounds")}
    assert statuses["I0490"] == "DONE"
