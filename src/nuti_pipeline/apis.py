"""공공 API 13개 (Java PublicApi). 행 수 · 호출 수는 2026-10-19~28 전량 수집 실측."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Provider(str, Enum):
    DATA_GO_KR = "DATA_GO_KR"
    FOOD_SAFETY = "FOOD_SAFETY"


class Condition(str, Enum):
    NONE = "NONE"        # 전량만
    CHNG_DT = "CHNG_DT"  # 변경일자 증분 (오늘 기준 최대 7일 전까지)
    EVL_YR = "EVL_YR"    # 평가연도


@dataclass(frozen=True)
class Api:
    name: str
    label: str
    provider: Provider
    path: str          # 공공데이터포털: 기관/서비스/오퍼레이션, 식품안전나라: 서비스 ID
    page_size: int
    condition: Condition
    daily_limit: int | None  # None = 설정값(DATAGOKR_DAILY_LIMIT)
    total_untrusted: bool = False  # C005: totalCount 를 못 믿음 → 빈/덜 찬 페이지까지


_D, _F = Provider.DATA_GO_KR, Provider.FOOD_SAFETY
_N, _C, _Y = Condition.NONE, Condition.CHNG_DT, Condition.EVL_YR

APIS: dict[str, Api] = {a.name: a for a in (
    Api("NUTRI_DB", "영양성분DB", _D, "1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02", 500, _N, None),
    Api("FOODQR_LIST", "푸드QR 목록", _D, "1471000/FoodQrInfoService01/getFoodQrProdList01", 100, _N, None),
    Api("FOODQR_NUTRITION", "푸드QR 영양", _D, "1471000/FoodQrInfoService01/getFoodQrProdNsd01", 100, _N, None),
    Api("FOODQR_MAKER", "푸드QR 제조", _D, "1471000/FoodQrInfoService01/getFoodQrProdMnfInfo01", 100, _N, None),
    Api("HACCP", "HACCP 포장표기", _D, "B553748/CertImgListServiceV3/getCertImgListServiceV3", 100, _N, None),
    Api("C005", "C005 바코드연계제품", _F, "C005", 1000, _C, 500, total_untrusted=True),
    Api("I2570", "I2570 유통바코드", _F, "I2570", 1000, _C, 500),
    Api("C002", "C002 품목제조보고 원재료", _F, "C002", 1000, _C, 1000),
    Api("C006", "C006 축산물 원재료", _F, "C006", 1000, _C, 1000),
    Api("I2852", "I2852 생산중단", _F, "I2852", 1000, _C, 1000),
    Api("I0490", "I0490 회수·판매중지", _F, "I0490", 1000, _N, 2000),
    Api("I0300", "I0300 생산실적", _F, "I0300", 1000, _Y, 1000),
    Api("I1420", "I1420 축산물 생산실적", _F, "I1420", 1000, _Y, 1000),
)}

# 34컬럼 계산에 꼭 필요한 11개 (I0300 · I1420 은 없어도 시도 17개로 지명 처리)
REQUIRED_FOR_BUILD: tuple[str, ...] = (
    "NUTRI_DB", "FOODQR_LIST", "FOODQR_NUTRITION", "FOODQR_MAKER", "HACCP",
    "C005", "I2570", "C002", "C006", "I2852", "I0490",
)
