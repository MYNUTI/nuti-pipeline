"""실제 공공 API 확인 — 손으로만 돌린다. 평소 pytest 에서는 건너뛴다.

    pytest -m live -s                       # 12개 전부 (호출 17회)
    pytest -m live -s -k C002               # 하나만
    pytest -m live -s -k "foodsafety"       # 식품안전나라만
    pytest -m live -s -k "datagokr"         # 공공데이터포털만

확인하는 것 (API 마다 1페이지만 부른다)
  1. 응답이 정상(OK)인가 — 아니면 코드 · 메시지와 함께 이유를 보여 준다
  2. 계산에 쓰는 필드가 응답에 다 있는가 (Java *_FIELDS + 분류용 필드, 페이지 전체 행 기준)
  3. 한 페이지 행 수가 맞는가 (페이지 크기만큼, 또는 전체가 더 적으면 전체만큼)
  4. CHNG_DT 증분 5개: 조건을 넣으면 전체 행 수가 줄어드는가 (조건 이름이 틀리면 오류 없이 전체가 옴)
  5. EVL_YR 생산실적 2개: 작년 조건으로 받아지는가

호출은 state.db 에 기록하지 않는다 (pipeline status 의 오늘 호출 수에 안 잡힘). 기관 쪽 한도에는 들어간다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from nuti_pipeline.apis import APIS, Api, Condition, Provider
from nuti_pipeline.collect.clients import Http, Page, Status, make_clients
from nuti_pipeline.collect.collector import MAX_INCREMENTAL_TOTAL
from nuti_pipeline.config import load_settings

pytestmark = pytest.mark.live

# 계산에 꼭 쓰는 필드 — 없으면 실패
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "NUTRI_DB": ("FOOD_CD", "FOOD_NM_KR", "DB_GRP_NM", "DB_CLASS_NM", "FOOD_REF_NM", "MAKER_NM",
                 "SELLER_MANUFAC_NM", "IMP_MANUFAC_NM", "ITEM_REPORT_NO", "Z10500",
                 "AMT_NUM1", "AMT_NUM13", "AMT_NUM6", "AMT_NUM7", "AMT_NUM4",
                 "AMT_NUM25", "AMT_NUM24", "AMT_NUM23", "AMT_NUM8", "AMT_NUM3"),
    "FOODQR_LIST": ("ELB_MNG_NO", "LGG_CD", "BRCD_NO", "PRDCT_NM", "CTV", "VLD_BGNG_YMD", "VER_NO", "BZENTY_NM"),
    "FOODQR_NUTRITION": ("ELB_MNG_NO", "IMRPT_NO", "BRCD_NO", "NTRTN_INDCT_SN", "NTRTN_INDCT_CD",
                         "NTRTN_INDCT_TCT", "NTRTN_INDCT_TCD", "NTRTN_ICUT_CTV", "NTRTN_ICUT_CVUCD",
                         "NIRWMT_NM", "CTA"),
    "FOODQR_MAKER": ("ELB_MNG_NO", "IMRPT_NO", "BRCD_NO", "BUES_NM"),
    "HACCP": ("prdlstReportNo", "barcode", "rawmtrl", "prdlstNm", "manufacture"),
    "C005": ("PRDLST_REPORT_NO", "BAR_CD", "PRDLST_NM", "BSSH_NM"),
    "I2570": ("PRDLST_REPORT_NO", "BRCD_NO", "PRDT_NM", "CMPNY_NM"),
    "C002": ("PRDLST_REPORT_NO", "RAWMTRL_NM", "CHNG_DT"),
    "C006": ("PRDLST_REPORT_NO", "RAWMTRL_NM", "CHNG_DT"),
    "I2852": ("PRDLST_REPORT_NO",),
    "I0490": ("PRDLST_REPORT_NO", "BRCDNO"),
    "I0300": ("SITE_ADDR",),
}

# 있어야 할 것 같지만 확인이 안 된 필드 — 없으면 경고만 출력
EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "FOODQR_LIST": ("FOOD_TYPE_CD_NM",),  # 분류용 (build.py)
}

DATAGOKR = [a for a in APIS.values() if a.provider is Provider.DATA_GO_KR]
FOODSAFETY = [a for a in APIS.values() if a.provider is Provider.FOOD_SAFETY]
CHNG_DT_APIS = [a for a in APIS.values() if a.condition is Condition.CHNG_DT]

HINTS = {
    "INFO-110": "식품안전나라에서 이 서비스 활용신청이 필요합니다",
    "INFO-100": "식품안전나라 인증키가 틀렸습니다",
    "HTML": "인증키가 틀렸거나 활용신청하지 않은 서비스입니다",
    "20": "공공데이터포털에서 이 API 활용신청 · 승인 상태를 확인하세요",
    "30": "등록되지 않은 공공데이터포털 키입니다 (Decoding 키인지 확인)",
    "22": "공공데이터포털 하루 한도를 넘었습니다",
    "HTTP_504": "기관 서버 응답 시간 초과 — 잠시 뒤 다시 확인하세요",
}


@pytest.fixture(scope="module")
def clients():
    s = load_settings()
    if not s.foodsafety_key or not s.datagokr_service_key:
        pytest.skip(".env 에 FOODSAFETY_KEY, DATAGOKR_SERVICE_KEY 를 채워야 합니다")
    http = Http()
    yield make_clients(http, s.foodsafety_key, s.datagokr_service_key)
    http.close()


def _call(clients, api: Api, cond: str | None = None) -> Page:
    page = clients[api.provider].fetch(api, 1, cond)
    label = f"{api.name} ({api.label})" + (f" 조건={cond}" if cond else "")
    print(f"\n[{label}] {page.status.value} {page.code} {page.message}")
    print(f"  전체 행 수: {page.total_count}  이번 페이지: {len(page.rows)}행")
    return page


def _assert_ok(page: Page, api: Api) -> None:
    if page.status is not Status.OK:
        hint = HINTS.get(page.code, "")
        pytest.fail(f"{api.name}: {page.status.value} {page.code} {page.message}" + (f" → {hint}" if hint else ""))


def _check_page(api: Api, page: Page) -> None:
    _assert_ok(page, api)
    assert page.rows, f"{api.name}: 정상 응답인데 행이 0개"

    # 필드 — 값이 비면 필드 자체를 빼고 주는 API 가 있어(HACCP barcode 등) 페이지 전체 행에서 모은다
    keys = set().union(*page.rows)
    missing = [f for f in REQUIRED_FIELDS[api.name] if f not in keys]
    soft_missing = [f for f in EXPECTED_FIELDS.get(api.name, ()) if f not in keys]
    if soft_missing:
        print(f"  ⚠ 확인 필요 — 응답에 없는 필드: {soft_missing}")
    print(f"  응답 필드 {len(keys)}개: {sorted(keys)}")
    assert not missing, f"{api.name}: 계산에 쓰는 필드가 응답에 없음 {missing}"

    # 페이지 크기 (C005 는 totalCount 를 못 믿으므로 행 수만 본다)
    assert len(page.rows) <= api.page_size, f"{api.name}: 한 페이지 {len(page.rows)}행 > 페이지 크기 {api.page_size}"
    if page.total_count is not None and not api.total_untrusted:
        assert len(page.rows) == min(api.page_size, page.total_count), \
            f"{api.name}: 이번 페이지 {len(page.rows)}행, 기대 {min(api.page_size, page.total_count)}행"


# ── 1. 공공데이터포털 5개: 전량 1페이지 ──────────────────────────────────

@pytest.mark.parametrize("api", DATAGOKR, ids=lambda a: f"datagokr-{a.name}")
def test_공공데이터포털(clients, api):
    _check_page(api, _call(clients, api))


# ── 2. 식품안전나라 7개: 조건 없이 1페이지 (I0300 은 작년 조건) ──

@pytest.mark.parametrize("api", FOODSAFETY, ids=lambda a: f"foodsafety-{a.name}")
def test_식품안전나라(clients, api):
    cond = str(date.today().year - 1) if api.condition is Condition.EVL_YR else None
    _check_page(api, _call(clients, api, cond))


# ── 3. 증분 5개: CHNG_DT 조건이 실제로 먹는가 ────────────────────────────

@pytest.mark.parametrize("api", CHNG_DT_APIS, ids=lambda a: f"foodsafety-chngdt-{a.name}")
def test_식품안전나라_증분_조건(clients, api):
    since = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
    page = _call(clients, api, since)
    if page.status is Status.NO_DATA:
        print("  최근 2일 바뀐 자료 없음 — 조건은 받아들여진 것으로 봄")
        return
    _assert_ok(page, api)
    if api.total_untrusted:
        # C005 는 전체 행 수가 요청 구간 크기를 그대로 돌려줘 믿을 수 없다. 조건이 무시되면 전체 목록이 와서 첫 페이지가 꽉 찬다
        if len(page.rows) == api.page_size:
            print(f"  ⚠ 최근 2일 증분인데 첫 페이지가 {api.page_size}행으로 꽉 참 — 조건이 무시됐을 수 있음, 첫 행 CHNG_DT 확인:"
                  f" {page.rows[0].get('CHNG_DT')}")
        return
    assert page.total_count is not None and page.total_count <= MAX_INCREMENTAL_TOTAL, \
        f"{api.name}: 증분인데 전체 {page.total_count}행 — 조건 이름이 무시된 것 같습니다"