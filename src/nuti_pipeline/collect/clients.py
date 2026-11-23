"""공공 API 클라이언트 2개 — 식품안전나라, 공공데이터포털 (Java FoodSafetyClient · DataGoKrClient · PublicApiHttp · ApiPage).

한 번 호출 = 한 페이지. 네트워크 오류도 예외로 던지지 않고 RETRY_LATER 결과로 돌려준다.
응답이 어떤 모양이든 Page 하나로 바꾸고, 그 다음 처리(계속 받기 · 멈추기 · 실패)는 수집기가 정한다.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..apis import Api, Provider

log = logging.getLogger(__name__)


class Status(str, Enum):
    OK = "OK"                          # 정상 (INFO-000 · 00)
    NO_DATA = "NO_DATA"                # 진짜 0건 (INFO-200 · 03)
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"  # 한도 초과 → 이 회차는 멈추고 나중에 이어 받음
    RETRY_LATER = "RETRY_LATER"        # 서버 오류 · 시간 초과 · 빈 응답 → 나중에 이어 받음
    KEY_REJECTED = "KEY_REJECTED"      # 인증키 오류 · 미신청 API → 회차 실패
    ERROR = "ERROR"                    # 그 밖의 오류 → 회차 실패


@dataclass
class Page:
    status: Status
    code: str
    message: str
    total_count: int | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    raw: str = field(default="", repr=False)  # 응답 본문 그대로 (probe --raw 로 확인용)


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except ValueError:
        return None


# ── HTTP ──────────────────────────────────────────────────────────────────

class Http:
    """시간 제한 필수(연결 10초 · 읽기 60초). 4xx · 5xx 도 예외 대신 (상태 코드, 본문)으로 돌려준다.
    본문은 바이트를 UTF-8 로 직접 읽는다(Content-Type 에 charset 이 없어 한글이 깨지는 것 방지).
    URL 은 이미 인코딩된 문자열을 그대로 쓴다 — httpx 는 %2B 같은 기존 인코딩을 다시 인코딩하지 않는다."""

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True)

    def get(self, url: str) -> tuple[int, str]:
        res = self._client.get(url)
        return res.status_code, res.content.decode("utf-8", errors="replace")

    def close(self) -> None:
        self._client.close()


class Client(Protocol):
    def fetch(self, api: Api, page: int, condition_value: str | None) -> Page: ...


# ── 식품안전나라 ──────────────────────────────────────────────────────────

class FoodSafetyClient:
    """http://openapi.foodsafetykorea.go.kr/api/{키}/{서비스ID}/json/{시작}/{끝}[/{조건}={값}]

    - 응답: {"C002":{"total_count":"…","row":[…],"RESULT":{"CODE":"INFO-000","MSG":"…"}}}
    - 키가 틀리거나 미신청이면 HTTP 200 에 HTML(alert) → 키 거부
    - 조건 이름을 틀리면 오류 없이 전체 목록이 옴 → 수집기가 증분 행 수로 걸러냄
    """
    BASE = "http://openapi.foodsafetykorea.go.kr/api/"
    QUOTA = {"INFO-300", "ERROR-503", "INFO-320"}
    RETRY = {"ERROR-500", "INFO-700"}
    KEY = {"INFO-100", "INFO-110"}  # INFO-110: 키는 맞지만 그 서비스 활용신청이 없음

    def __init__(self, http: Http, key: str) -> None:
        self.http = http
        self.key = key.strip()

    def fetch(self, api: Api, page: int, condition_value: str | None) -> Page:
        if not self.key:
            return Page(Status.KEY_REJECTED, "NO_KEY", "FOODSAFETY_KEY 가 비어 있습니다")
        start = (page - 1) * api.page_size + 1
        end = page * api.page_size
        url = f"{self.BASE}{self.key}/{api.path}/json/{start}/{end}"
        if condition_value and api.condition.value != "NONE":
            url += f"/{api.condition.value}={condition_value}"
        try:
            status, body = self.http.get(url)
        except httpx.HTTPError as e:
            log.warning("%s page=%d 호출 실패 — %s", api.name, page, type(e).__name__)  # URL 에 키가 있어 메시지는 남기지 않음
            return Page(Status.RETRY_LATER, "NETWORK", type(e).__name__)
        if status >= 500:
            return Page(Status.RETRY_LATER, f"HTTP_{status}", "서버 오류", raw=body)
        page = self.parse(api.path, body)
        page.raw = body
        return page

    @classmethod
    def parse(cls, service_id: str, body: str | None) -> Page:
        b = (body or "").strip()
        if not b:
            return Page(Status.RETRY_LATER, "EMPTY", "빈 응답")
        if b.startswith("<") or "alert(" in b:
            return Page(Status.KEY_REJECTED, "HTML", "JSON 이 아닌 HTML 응답 — 인증키가 틀렸거나 미신청 API")
        try:
            root = json.loads(b)
        except ValueError as e:
            return Page(Status.ERROR, "PARSE", f"JSON 을 읽을 수 없습니다: {e}")
        if not isinstance(root, dict):
            return Page(Status.ERROR, "PARSE", "JSON 최상위가 객체가 아닙니다")
        node = root.get(service_id) if isinstance(root.get(service_id), dict) else root
        result = node.get("RESULT")
        if not isinstance(result, dict):
            return Page(Status.ERROR, "NO_RESULT", "RESULT 가 없습니다")
        code = str(result.get("CODE", ""))
        msg = str(result.get("MSG", ""))

        if code == "INFO-000":
            rows = node.get("row")
            rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
            return Page(Status.OK, code, msg, _to_int(node.get("total_count")), rows)
        if code == "INFO-200":
            return Page(Status.NO_DATA, code, msg, 0, [])
        if code in cls.QUOTA:
            return Page(Status.QUOTA_EXCEEDED, code, msg)
        if code in cls.RETRY:
            return Page(Status.RETRY_LATER, code, msg)
        if code in cls.KEY:
            return Page(Status.KEY_REJECTED, code, msg)
        return Page(Status.ERROR, code, msg)


# ── 공공데이터포털 ────────────────────────────────────────────────────────

_XML_CODE = re.compile(r"<(returnReasonCode|resultCode)>\s*([^<\s]+)\s*</")
_XML_MSG = re.compile(r"<(returnAuthMsg|resultMsg|errMsg)>\s*([^<]*)</")


class DataGoKrClient:
    """https://apis.data.go.kr/{경로}?serviceKey={키}&pageNo=N&numOfRows=M&type=json

    - Decoding 키를 받아 여기서 한 번만 URL 인코딩 (Encoding 키를 넣으면 두 번 인코딩돼 실패)
    - 응답 모양이 서비스마다 다름: {header, body} / {response:{header, body}},
      items 가 배열 · {item:[…]} · {item:{…}} · 배열 안 {item:{…}} — 전부 처리
    - 게이트웨이 오류(한도 · 키)는 type=json 이어도 XML → 정규식으로 코드 판정
    """
    BASE = "https://apis.data.go.kr/"
    SUCCESS = {"00", "OK"}  # HACCP(B553748) 는 "OK"
    QUOTA = {"22"}
    KEY = {"20", "30", "31", "32"}
    RETRY = {"01", "02", "04", "05"}

    def __init__(self, http: Http, service_key: str) -> None:
        self.http = http
        self.service_key = service_key.strip()

    def fetch(self, api: Api, page: int, condition_value: str | None,
              extra: dict[str, str] | None = None) -> Page:
        """extra: 확인용 추가 파라미터 (probe --param). 수집 때는 쓰지 않는다."""
        if not self.service_key:
            return Page(Status.KEY_REJECTED, "NO_KEY", "DATAGOKR_SERVICE_KEY 가 비어 있습니다")
        url = (f"{self.BASE}{api.path}?serviceKey={quote(self.service_key, safe='')}"
               f"&pageNo={page}&numOfRows={api.page_size}&type=json")
        for k, v in {**dict(api.extra_params), **(extra or {})}.items():
            url += f"&{quote(k, safe='')}={quote(v, safe='')}"
        try:
            status, body = self.http.get(url)
        except httpx.HTTPError as e:
            log.warning("%s page=%d 호출 실패 — %s", api.name, page, type(e).__name__)
            return Page(Status.RETRY_LATER, "NETWORK", type(e).__name__)
        if status >= 500 and not body.strip().startswith("<"):
            return Page(Status.RETRY_LATER, f"HTTP_{status}", "서버 오류", raw=body)
        result = self.parse(body)
        result.raw = body
        return result

    @classmethod
    def parse(cls, body: str | None) -> Page:
        b = (body or "").strip()
        if not b:
            return Page(Status.RETRY_LATER, "EMPTY", "빈 응답")
        if "alert(" in b:
            return Page(Status.KEY_REJECTED, "HTML", "HTML 응답 — 인증키 확인 필요")
        if b.startswith("<"):
            return cls._parse_xml(b)
        try:
            root = json.loads(b)
        except ValueError as e:
            return Page(Status.ERROR, "PARSE", f"JSON 을 읽을 수 없습니다: {e}")
        if not isinstance(root, dict):
            return Page(Status.ERROR, "PARSE", "JSON 최상위가 객체가 아닙니다")
        top = root["response"] if isinstance(root.get("response"), dict) else root
        header = top.get("header") if isinstance(top.get("header"), dict) else {}
        body_node = top.get("body") if isinstance(top.get("body"), dict) else {}
        code = str(header.get("resultCode", ""))
        msg = str(header.get("resultMsg", header.get("resultMessage", "")))  # HACCP 는 resultMessage

        if code in cls.SUCCESS:
            return Page(Status.OK, code, msg, _to_int(body_node.get("totalCount")), items(body_node.get("items")))
        if code == "03":
            return Page(Status.NO_DATA, code, msg, 0, [])
        return cls._classify(code, msg)

    @classmethod
    def _parse_xml(cls, b: str) -> Page:
        c = _XML_CODE.search(b)
        m = _XML_MSG.search(b)
        code = c.group(2) if c else "XML"
        msg = m.group(2).strip() if m else "XML 응답"
        if code == "00":
            return Page(Status.ERROR, code, "type=json 인데 XML 로 정상 응답 — 파라미터 확인 필요")
        if code == "03":
            return Page(Status.NO_DATA, code, msg, 0, [])
        return cls._classify(code, msg)

    @classmethod
    def _classify(cls, code: str, msg: str) -> Page:
        if code in cls.QUOTA:
            return Page(Status.QUOTA_EXCEEDED, code, msg)
        if code in cls.KEY:
            return Page(Status.KEY_REJECTED, code, msg)
        if code in cls.RETRY:
            return Page(Status.RETRY_LATER, code, msg)
        return Page(Status.ERROR, code, msg)


def items(node: Any) -> list[dict[str, Any]]:
    """items 가 배열 · {item:[…]} · {item:{…}} · 배열 안 {item:{…}} 어느 모양이든 행 목록으로."""
    if node is None:
        return []
    if isinstance(node, dict):
        item = node.get("item")
        if item is None:
            return []
        return items(item if isinstance(item, list) else [item])
    if isinstance(node, list):
        out = []
        for o in node:
            if not isinstance(o, dict):
                continue
            if len(o) == 1 and isinstance(o.get("item"), dict):
                o = o["item"]
            out.append(o)
        return out
    return []


def make_clients(http: Http, foodsafety_key: str, datagokr_key: str) -> dict[Provider, Client]:
    return {
        Provider.FOOD_SAFETY: FoodSafetyClient(http, foodsafety_key),
        Provider.DATA_GO_KR: DataGoKrClient(http, datagokr_key),
    }