"""34컬럼 정의 — product_master.csv 와 같은 이름 · 순서."""
from __future__ import annotations

# 열량 · 나트륨 · 탄수화물 · 당류 · 지방 · 트랜스지방 · 포화지방 · 콜레스테롤 · 식이섬유 · 단백질
NUTRIENT_COLUMNS: tuple[str, ...] = (
    "calories_kcal", "sodium_mg", "carbohydrate_g", "sugar_g", "fat_g",
    "trans_fat_g", "saturated_fat_g", "cholesterol_mg", "dietary_fiber_g", "protein_g",
)
PER_100G_COLUMNS: tuple[str, ...] = tuple(f"{c}_per_100g" for c in NUTRIENT_COLUMNS)

# content_hash 대상 30칸 — 이 순서 그대로 이어 붙인다 (ContentHashCalculator.HASH_COLUMNS)
HASH_COLUMNS: tuple[str, ...] = (
    "barcode", "report_no", "source", "product_name", "brand", "category", "maker", "ingredients", "serving_size",
    *NUTRIENT_COLUMNS,
    *PER_100G_COLUMNS,
    "is_discontinued",
)

# 34컬럼 — CSV 헤더 순서
MASTER_COLUMNS: tuple[str, ...] = (
    "master_id",
    *HASH_COLUMNS,
    "content_hash",
    "created_at",
    "updated_at",
)

NUMERIC_COLUMNS: frozenset[str] = frozenset(NUTRIENT_COLUMNS + PER_100G_COLUMNS)

assert len(HASH_COLUMNS) == 30
assert len(MASTER_COLUMNS) == 34
