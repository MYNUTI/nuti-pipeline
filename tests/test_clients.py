"""FoodSafetyClientTest · DataGoKrClientTest 이식 + URL 만들기."""
from __future__ import annotations

from nuti_pipeline.apis import APIS
from nuti_pipeline.collect.clients import DataGoKrClient, FoodSafetyClient, Status


# ── 식품안전나라 ──

def _fs_result(svc: str, code: str) -> str:
    return '{"%s":{"RESULT":{"MSG":"-","CODE":"%s"}}}' % (svc, code)


def test_식품안전나라_정상_응답이면_행과_전체_수를_읽는다():
    body = """{"C005":{"total_count":"2","row":[{"BAR_CD":"8801234567890","PRDLST_NM":"사과주스"},
              {"BAR_CD":"8801234567891","PRDLST_NM":"배주스"}],
              "RESULT":{"MSG":"정상처리되었습니다.","CODE":"INFO-000"}}}"""
    p = FoodSafetyClient.parse("C005", body)
    assert p.status is Status.OK
    assert p.total_count == 2
    assert len(p.rows) == 2
    assert p.rows[0]["PRDLST_NM"] == "사과주스"


def test_식품안전나라_INFO_200은_데이터_없음():
    p = FoodSafetyClient.parse("C002", '{"C002":{"RESULT":{"MSG":"없음","CODE":"INFO-200"}}}')
    assert p.status is Status.NO_DATA
    assert p.rows == []


def test_식품안전나라_결과가_서비스_이름_밖에_있어도_읽는다():
    p = FoodSafetyClient.parse("C005", '{"RESULT":{"MSG":"인증키가 유효하지 않습니다.","CODE":"INFO-100"}}')
    assert p.status is Status.KEY_REJECTED


def test_식품안전나라_한도_초과와_재시도를_구분한다():
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "INFO-300")).status is Status.QUOTA_EXCEEDED
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "ERROR-503")).status is Status.QUOTA_EXCEEDED
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "INFO-320")).status is Status.QUOTA_EXCEEDED
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "ERROR-500")).status is Status.RETRY_LATER
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "INFO-700")).status is Status.RETRY_LATER
    assert FoodSafetyClient.parse("I2570", _fs_result("I2570", "ERROR-336")).status is Status.ERROR


def test_식품안전나라_HTML_응답은_키_거부():
    assert FoodSafetyClient.parse("C005", "<script>alert('인증키를 확인하세요');</script>").status is Status.KEY_REJECTED


def test_식품안전나라_빈_응답은_재시도():
    assert FoodSafetyClient.parse("C005", "  ").status is Status.RETRY_LATER


class _FakeHttp:
    def __init__(self, body: str = "", status: int = 200):
        self.urls: list[str] = []
        self.body, self.status = body, status

    def get(self, url: str):
        self.urls.append(url)
        return self.status, self.body


def test_식품안전나라_URL은_페이지_구간과_조건을_붙인다():
    http = _FakeHttp(_fs_result("C002", "INFO-200"))
    FoodSafetyClient(http, "KEY").fetch(APIS["C002"], 3, "20261120")
    assert http.urls == ["http://openapi.foodsafetykorea.go.kr/api/KEY/C002/json/2001/3000/CHNG_DT=20261120"]


def test_식품안전나라_서버_오류는_재시도():
    p = FoodSafetyClient(_FakeHttp("err", 502), "KEY").fetch(APIS["C002"], 1, None)
    assert p.status is Status.RETRY_LATER and p.code == "HTTP_502"


def test_키가_비면_키_거부():
    assert FoodSafetyClient(_FakeHttp(), "").fetch(APIS["C002"], 1, None).status is Status.KEY_REJECTED
    assert DataGoKrClient(_FakeHttp(), " ").fetch(APIS["NUTRI_DB"], 1, None).status is Status.KEY_REJECTED


# ── 공공데이터포털 ──

def test_공공데이터포털_items가_배열이면_그대로_읽는다():
    body = """{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE."},
              "body":{"pageNo":1,"totalCount":3,"numOfRows":100,"items":[{"FOOD_CD":"A1"},{"FOOD_CD":"A2"},{"FOOD_CD":"A3"}]}}"""
    p = DataGoKrClient.parse(body)
    assert p.status is Status.OK
    assert p.total_count == 3
    assert [r["FOOD_CD"] for r in p.rows] == ["A1", "A2", "A3"]


def test_공공데이터포털_response로_감싸고_item이_배열이어도_읽는다():
    body = """{"response":{"header":{"resultCode":"00","resultMsg":"OK"},
              "body":{"totalCount":"2","items":{"item":[{"prdlstNm":"두부"},{"prdlstNm":"우유"}]}}}}"""
    p = DataGoKrClient.parse(body)
    assert p.total_count == 2
    assert [r["prdlstNm"] for r in p.rows] == ["두부", "우유"]


def test_공공데이터포털_item이_하나면_객체로_와도_한_행():
    body = """{"response":{"header":{"resultCode":"00","resultMsg":"OK"},
              "body":{"totalCount":1,"items":{"item":{"prdlstNm":"두부"}}}}}"""
    assert len(DataGoKrClient.parse(body).rows) == 1


def test_공공데이터포털_배열_안에_item으로_한_번_더_감싸도_벗긴다():
    body = """{"header":{"resultCode":"00","resultMsg":"OK"},
              "body":{"totalCount":2,"items":[{"item":{"PRDLST_NM":"김치"}},{"item":{"PRDLST_NM":"만두"}}]}}"""
    assert [r["PRDLST_NM"] for r in DataGoKrClient.parse(body).rows] == ["김치", "만두"]


def test_공공데이터포털_결과코드_03은_데이터_없음():
    assert DataGoKrClient.parse('{"header":{"resultCode":"03","resultMsg":"NODATA_ERROR"},"body":{}}').status is Status.NO_DATA


def test_공공데이터포털_게이트웨이_XML_오류를_읽는다():
    quota = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
        <returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>
        <returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""
    key = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
        <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
        <returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""
    assert DataGoKrClient.parse(quota).status is Status.QUOTA_EXCEEDED
    assert DataGoKrClient.parse(key).status is Status.KEY_REJECTED


def test_공공데이터포털_Decoding_키를_한_번만_인코딩한다():
    http = _FakeHttp('{"header":{"resultCode":"03","resultMsg":"-"}}')
    DataGoKrClient(http, "ab+c/d==").fetch(APIS["NUTRI_DB"], 2, None)
    assert http.urls == ["https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02"
                         "?serviceKey=ab%2Bc%2Fd%3D%3D&pageNo=2&numOfRows=500&type=json"]


def test_공공데이터포털_5xx라도_XML이면_본문으로_판정():
    xml = "<r><returnReasonCode>22</returnReasonCode></r>"
    assert DataGoKrClient(_FakeHttp(xml, 500), "K").fetch(APIS["HACCP"], 1, None).status is Status.QUOTA_EXCEEDED
    assert DataGoKrClient(_FakeHttp("oops", 500), "K").fetch(APIS["HACCP"], 1, None).status is Status.RETRY_LATER


def test_식품안전나라_활용신청이_없으면_키_거부():
    body = '{"I0490":{"RESULT":{"MSG":"인증키는 확인되었으나 해당 서비스의 활용신청 내역이 없습니다.","CODE":"INFO-110"}}}'
    assert FoodSafetyClient.parse("I0490", body).status is Status.KEY_REJECTED


def test_HACCP는_returnType_json을_붙이고_성공_코드_OK를_정상으로_본다():
    http = _FakeHttp('{"header":{"resultCode":"03","resultMessage":"-"}}')
    DataGoKrClient(http, "K").fetch(APIS["HACCP"], 1, None)
    assert http.urls[0].endswith("&type=json&returnType=json")
    body = """{"body":{"items":[{"item":{"prdlstReportNo":"201903787252","barcode":"8 809678 470007","rawmtrl":"군고구마"}},
              {"item":{"prdlstReportNo":"201904962715","rawmtrl":"찹쌀"}}],"numOfRows":"100","pageNo":"1","totalCount":"14602"},
              "header":{"resultCode":"OK","resultMessage":"success"}}"""
    p = DataGoKrClient.parse(body)
    assert p.status is Status.OK
    assert p.message == "success"
    assert p.total_count == 14602
    assert [r["prdlstReportNo"] for r in p.rows] == ["201903787252", "201904962715"]