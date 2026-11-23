"""state.db (SQLite, data 볼륨) — 1회성 컨테이너가 다음 실행에 넘겨줄 상태 전부.

| 표 | Java 대응 | 용도 |
| rounds | PublicApiRound | 회차: API 하나를 처음부터 끝까지 한 번 받는 단위 |
| call_log | PublicApiCallLog | 호출 한도 계산 (30일 지나면 삭제) |
| raw | PublicApiRaw | 원본 행. 페이지마다 커밋 → 끊겨도 next_page 부터 이어 받음 |
| brand_groups | BrandGroup | 묶음 구성 (다시 만들기 어려움 → 백업) |
| category_map | (DB 이어 쓰기 대신) | master_id → 분류 코드 (다시 만들기 어려움 → 백업) |
| category_review | SyncReviewRepository | 분류가 안 정해진 새 제품 (이번 CSV 에서 빠짐) |
| builds | — | CSV 생성 이력. 마지막 build 이후 새로 끝난 회차가 있는지 판단 |

계산은 status='DONE' 회차의 raw 만 읽는다 → 받는 중인 회차는 안 보이고 이전 스냅샷으로 계산.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id              INTEGER PRIMARY KEY,
    api             TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('FULL', 'INCREMENTAL')),
    cond_value      TEXT,                 -- CHNG_DT=YYYYMMDD / EVL_YR=YYYY
    status          TEXT NOT NULL CHECK (status IN ('RUNNING', 'DONE', 'FAILED')),
    next_page       INTEGER NOT NULL DEFAULT 1,
    total_count     INTEGER,
    rows_received   INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,        -- KST, 'YYYY-MM-DDTHH:MM:SS'
    finished_at     TEXT,
    last_code       TEXT,                 -- 마지막 응답 코드 또는 실패 사유 (COUNT_MISMATCH · EMPTY_FULL …)
    last_message    TEXT
);
-- API 당 RUNNING 회차는 1개만
CREATE UNIQUE INDEX IF NOT EXISTS ux_rounds_running ON rounds(api) WHERE status = 'RUNNING';
CREATE INDEX IF NOT EXISTS ix_rounds_api_status ON rounds(api, status, finished_at);

CREATE TABLE IF NOT EXISTS call_log (
    id          INTEGER PRIMARY KEY,
    api         TEXT NOT NULL,
    called_at   TEXT NOT NULL,            -- KST, 'YYYY-MM-DDTHH:MM:SS'
    code        TEXT NOT NULL             -- 응답 코드 (INFO-000 · 22 · NETWORK …)
);
CREATE INDEX IF NOT EXISTS ix_call_log_api_time ON call_log(api, called_at);

CREATE TABLE IF NOT EXISTS raw (
    id          INTEGER PRIMARY KEY,
    api         TEXT NOT NULL,
    round_id    INTEGER NOT NULL REFERENCES rounds(id),
    row_hash    TEXT NOT NULL,            -- data 의 sha256. 같은 회차 안 같은 행은 한 번만
    data        TEXT NOT NULL             -- 응답 한 행 JSON (큰 API 는 필요한 필드만 — collect/collector.py KEEP_FIELDS)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_round_hash ON raw(round_id, row_hash);
CREATE INDEX IF NOT EXISTS ix_raw_api_round ON raw(api, round_id);

CREATE TABLE IF NOT EXISTS brand_groups (
    food_cd             TEXT PRIMARY KEY,
    group_key           TEXT NOT NULL,
    is_representative   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_brand_groups_key ON brand_groups(group_key);

CREATE TABLE IF NOT EXISTS category_map (
    master_id   TEXT PRIMARY KEY,
    category    TEXT,                     -- 중분류 코드. NULL = 분류 제외로 확정
    origin      TEXT NOT NULL CHECK (origin IN ('seed', 'rule', 'manual')),
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_review (
    master_id     TEXT PRIMARY KEY,
    product_name  TEXT,
    maker         TEXT,
    food_type     TEXT,                   -- FOOD_REF_NM / FOOD_TYPE_CD_NM
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id              INTEGER PRIMARY KEY,
    built_at        TEXT NOT NULL,
    file_name       TEXT,                 -- NULL = 계산만 하고 파일은 안 만듦
    row_count       INTEGER,
    held_back       INTEGER,              -- 분류 대기로 뺀 새 제품 수
    max_round_id    INTEGER NOT NULL      -- 이 build 가 본 마지막 DONE 회차 id
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn