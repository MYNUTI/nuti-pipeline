"""묶음 구성 (Java BrandGrouping, brand.md 조회표 3) — 영양성분DB 행을 묶음(group_key)으로 나누고 묶음마다 대표 행을 정한다.

- 이미 묶음이 정해진 food_cd 는 바꾸지 않는다. 매번 다시 묶으면 제품명이 조금 바뀔 때 다른 묶음으로 옮겨 가
  묶음 키에 걸린 쿠팡 보정 · 11-12 고침이 풀린다.
- 새 food_cd 는 제품명 · 제조사 · 총내용량(FOOD_NM_KR, MAKER_NM, Z10500)이 같은 기존 묶음에 넣는다.
  없으면 새 묶음 — 키는 세 값의 sha256 앞 16자리 (겹치면 #번호를 붙여 다시).
- 대표 행: 저장된 대표가 지금도 있으면 그 행, 없으면 묶음에서 가장 작은 food_cd.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .rules import norm


@dataclass(frozen=True)
class Stored:
    group_key: str
    representative: bool


@dataclass(frozen=True)
class Member:
    food_cd: str
    name: str | None
    maker: str | None
    weight: str | None


@dataclass(frozen=True)
class Assigned:
    """새로 정한 묶음 한 줄 — 저장할 것."""
    food_cd: str
    group_key: str
    representative: bool


@dataclass
class GroupingResult:
    group_of: dict[str, str] = field(default_factory=dict)            # food_cd → group_key
    representative_of: dict[str, str] = field(default_factory=dict)   # group_key → 대표 food_cd
    assigned: list[Assigned] = field(default_factory=list)


def triple(m: Member) -> str:
    return norm(m.name) + "\x1f" + norm(m.maker) + "\x1f" + norm(m.weight)


def new_key(t: str, used: set[str]) -> str:
    """sha256 앞 16자리. 이미 쓰는 키와 겹치면 뒤에 번호를 붙여 다시."""
    i = 0
    while True:
        k = hashlib.sha256((t if i == 0 else f"{t}#{i}").encode("utf-8")).hexdigest()[:16]
        if k not in used:
            return k
        i += 1


def assign(members: Iterable[Member], stored: Mapping[str, Stored]) -> GroupingResult:
    group_of: dict[str, str] = {}
    triple_to_group: dict[str, str] = {}
    used = {s.group_key for s in stored.values()}
    ordered = sorted(members, key=lambda m: m.food_cd)

    # 1) 이미 정해진 행 — 세 값 → 묶음 지도 (먼저 본 것)
    for m in ordered:
        s = stored.get(m.food_cd)
        if s is None:
            continue
        group_of[m.food_cd] = s.group_key
        triple_to_group.setdefault(triple(m), s.group_key)

    # 2) 새 행 — 같은 세 값의 묶음에 넣거나 새 묶음
    assigned: list[Assigned] = []
    new_groups: set[str] = set()
    for m in ordered:
        if m.food_cd in group_of:
            continue
        t = triple(m)
        g = triple_to_group.get(t)
        if g is None:
            g = new_key(t, used)
            used.add(g)
            triple_to_group[t] = g
            new_groups.add(g)
        group_of[m.food_cd] = g
        # 새 묶음의 첫 행(가장 작은 food_cd)이 대표 — 정렬해서 돌므로 처음 넣는 행
        rep = g in new_groups
        new_groups.discard(g)
        assigned.append(Assigned(m.food_cd, g, rep))

    # 3) 대표 행
    rep_of: dict[str, str] = {}
    smallest: dict[str, str] = {}
    new_rep = {a.food_cd for a in assigned if a.representative}
    for m in ordered:
        g = group_of[m.food_cd]
        smallest.setdefault(g, m.food_cd)
        s = stored.get(m.food_cd)
        if (s is not None and s.representative) or m.food_cd in new_rep:
            rep_of.setdefault(g, m.food_cd)
    for g, fc in smallest.items():
        rep_of.setdefault(g, fc)

    return GroupingResult(group_of, rep_of, assigned)
