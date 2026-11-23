# nuti-pipeline

공공 API 13개 수집 → 34컬럼 계산 → `{DATA_DIR}/csv/product_master_YYYYMMDD.csv`.
api(Spring)는 이 CSV만 읽어 RDS에 반영한다. 파이프라인은 DB에 접속하지 않는다.

## 로컬

```bash
cp .env.example .env      # 키 채우기
uv sync
uv run pytest
uv run pipeline init
uv run pipeline verify-hash path/to/product_master.csv
```

## 명령

| 명령 | 언제 |
| --- | --- |
| `collect` | cron 10분마다 |
| `build` | cron 하루 1회 (07시 전후) |
| `categorize export / import` | 분류 검토 대기가 있을 때 |
| `status` | 수시로 |

상태는 전부 `{DATA_DIR}/state.db`(SQLite) 하나에 있다. 설명은 `src/nuti_pipeline/state/db.py` 맨 위.
