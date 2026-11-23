"""환경변수 설정. 로컬은 .env, 컨테이너는 compose 의 env_file 로 받는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Settings:
    foodsafety_key: str
    datagokr_service_key: str
    datagokr_daily_limit: int
    data_dir: Path
    log_level: str

    @property
    def csv_dir(self) -> Path:
        return self.data_dir / "csv"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "pipeline.lock"

    def require_keys(self) -> None:
        """수집할 때만 키가 필요하다. build · verify 는 키 없이 돈다."""
        missing = [n for n, v in (("FOODSAFETY_KEY", self.foodsafety_key),
                                  ("DATAGOKR_SERVICE_KEY", self.datagokr_service_key)) if not v]
        if missing:
            raise SystemExit(f"환경변수가 비어 있습니다: {', '.join(missing)} (.env.example 참고)")


def load_settings() -> Settings:
    load_dotenv(override=False)
    return Settings(
        foodsafety_key=os.getenv("FOODSAFETY_KEY", "").strip(),
        datagokr_service_key=os.getenv("DATAGOKR_SERVICE_KEY", "").strip(),
        datagokr_daily_limit=int(os.getenv("DATAGOKR_DAILY_LIMIT", "1000")),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
