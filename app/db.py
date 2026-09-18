"""SQLite 数据访问层。

三张表：
- allergens      规范过敏原（类别）字典，预置 GB 7718-2025 的 8 类
- ingredient_map 原料/配料 -> 过敏原类别的知识字典
- recipes        已保存的配方版本
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.environ.get("ALLERGEN_DB", "/data/allergen.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS allergens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ingredient_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient TEXT NOT NULL UNIQUE,
    allergen TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    ingredients_text TEXT NOT NULL,
    declared_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# 预置：GB 7718-2025 推荐致敏物标识的 8 类食品
SEED_ALLERGENS: list[tuple[str, str, str]] = [
    ("麸质谷物", "麸质,麸质蛋白,谷物蛋白,小麦,大麦,黑麦,燕麦,面筋", "含麸质的谷物及其制品"),
    ("甲壳类动物", "甲壳类,甲壳动物,虾,蟹,龙虾,虾酱", "甲壳纲类动物及其制品"),
    ("鱼类", "鱼,鱼肉,鱼油,鱼露", "鱼类及其制品"),
    ("蛋类", "蛋,鸡蛋,鸭蛋,蛋液,蛋黄,蛋清", "蛋类及其制品"),
    ("花生", "花生仁,花生酱,花生蛋白", "花生及其制品"),
    ("大豆", "黄豆,豆类,豆制品,大豆蛋白,大豆磷脂,卵磷脂", "豆类及其制品（以大豆为主）"),
    ("乳", "乳制品,牛奶,奶粉,奶油,乳糖,乳清,干酪,芝士,酪蛋白", "乳及乳制品（包括乳糖）"),
    ("坚果", "坚果类,果仁,杏仁,腰果,榛子,核桃,开心果,夏威夷果,巴旦木", "坚果及其果仁类制品"),
]

SEED_INGREDIENTS: list[tuple[str, str, str]] = [
    # 麸质谷物
    ("小麦粉", "麸质谷物", ""),
    ("面粉", "麸质谷物", ""),
    ("全麦粉", "麸质谷物", ""),
    ("淀粉", "", "小麦淀粉除外；默认小麦来源淀粉可单独改配为麸质谷物"),
    ("小麦淀粉", "麸质谷物", ""),
    ("麦芽", "麸质谷物", ""),
    ("麦芽糊精", "", "小麦来源时含麸质，玉米来源不含"),
    ("酱油", "麸质谷物", "传统酿造酱油以小麦为原料"),
    ("面筋", "麸质谷物", ""),
    ("麸皮", "麸质谷物", ""),
    ("燕麦", "麸质谷物", ""),
    # 甲壳类
    ("虾", "甲壳类动物", ""),
    ("虾仁", "甲壳类动物", ""),
    ("蟹", "甲壳类动物", ""),
    ("蟹肉", "甲壳类动物", ""),
    ("虾酱", "甲壳类动物", ""),
    ("磷虾", "甲壳类动物", ""),
    # 鱼类
    ("鱼肉", "鱼类", ""),
    ("鱼糜", "鱼类", ""),
    ("鱼油", "鱼类", ""),
    ("鱼露", "鱼类", ""),
    ("金枪鱼", "鱼类", ""),
    # 蛋类
    ("鸡蛋", "蛋类", ""),
    ("蛋液", "蛋类", ""),
    ("蛋黄", "蛋类", ""),
    ("蛋清", "蛋类", ""),
    ("全蛋粉", "蛋类", ""),
    ("蛋黄酱", "蛋类", ""),
    # 花生
    ("花生仁", "花生", ""),
    ("花生", "花生", ""),
    ("花生酱", "花生", ""),
    ("花生蛋白", "花生", ""),
    ("花生碎", "花生", ""),
    # 大豆
    ("大豆", "大豆", ""),
    ("黄豆", "大豆", ""),
    ("大豆油", "", "精炼大豆油通常不视为致敏原，可按实际情况改配"),
    ("大豆蛋白", "大豆", ""),
    ("大豆磷脂", "大豆", ""),
    ("卵磷脂", "大豆", "未注明来源时多为大豆来源"),
    ("豆腐", "大豆", ""),
    ("豆粉", "大豆", ""),
    ("纳豆", "大豆", ""),
    # 乳
    ("牛奶", "乳", ""),
    ("全脂乳粉", "乳", ""),
    ("乳粉", "乳", ""),
    ("奶粉", "乳", ""),
    ("稀奶油", "乳", ""),
    ("奶油", "乳", ""),
    ("黄油", "乳", ""),
    ("乳清粉", "乳", ""),
    ("乳清蛋白", "乳", ""),
    ("乳糖", "乳", ""),
    ("干酪", "乳", ""),
    ("芝士", "乳", ""),
    ("酪蛋白", "乳", ""),
    ("炼乳", "乳", ""),
    ("酸奶", "乳", ""),
    # 坚果
    ("杏仁", "坚果", ""),
    ("扁桃仁", "坚果", "巴旦木/扁桃仁为规范名称"),
    ("巴旦木", "坚果", ""),
    ("腰果", "坚果", ""),
    ("榛子", "坚果", ""),
    ("核桃", "坚果", ""),
    ("核桃仁", "坚果", ""),
    ("开心果", "坚果", ""),
    ("夏威夷果", "坚果", ""),
    ("坚果碎", "坚果", ""),
]


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed: bool = True) -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if seed:
            for name, aliases, note in SEED_ALLERGENS:
                conn.execute(
                    "INSERT OR IGNORE INTO allergens (name, aliases, note) VALUES (?, ?, ?)",
                    (name, aliases, note),
                )
            for ingredient, allergen, note in SEED_INGREDIENTS:
                conn.execute(
                    "INSERT OR IGNORE INTO ingredient_map (ingredient, allergen, note) VALUES (?, ?, ?)",
                    (ingredient, allergen, note),
                )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------- 配方 ----------------

def create_recipe(name: str, version: str, ingredients_text: str, declared_text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO recipes (name, version, ingredients_text, declared_text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name.strip(), version.strip(), ingredients_text.strip(), declared_text.strip(), _now()),
        )
        return int(cur.lastrowid)


def list_recipes() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute(
            "SELECT * FROM recipes ORDER BY id DESC"
        ))


def get_recipe(recipe_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()


def update_recipe(recipe_id: int, name: str, version: str,
                  ingredients_text: str, declared_text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE recipes SET name=?, version=?, ingredients_text=?, declared_text=? WHERE id=?",
            (name.strip(), version.strip(), ingredients_text.strip(), declared_text.strip(), recipe_id),
        )


def delete_recipe(recipe_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))


# ---------------- 过敏原类别 ----------------

def list_allergens() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute("SELECT * FROM allergens ORDER BY id"))


def add_allergen(name: str, aliases: str, note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO allergens (name, aliases, note) VALUES (?, ?, ?)",
            (name.strip(), aliases.strip(), note.strip()),
        )


# ---------------- 原料字典 ----------------

def list_mappings() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute(
            "SELECT m.*, a.note AS allergen_note FROM ingredient_map m "
            "LEFT JOIN allergens a ON m.allergen = a.name ORDER BY m.allergen, m.ingredient"
        ))


def add_mapping(ingredient: str, allergen: str, note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ingredient_map (ingredient, allergen, note) VALUES (?, ?, ?)",
            (ingredient.strip(), allergen.strip(), note.strip()),
        )


def update_mapping(mapping_id: int, ingredient: str, allergen: str, note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE ingredient_map SET ingredient=?, allergen=?, note=? WHERE id=?",
            (ingredient.strip(), allergen.strip(), note.strip(), mapping_id),
        )


def delete_mapping(mapping_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM ingredient_map WHERE id=?", (mapping_id,))


def load_knowledge() -> dict[str, Any]:
    """组装审查所需的知识：原料名 -> 过敏原，过敏原别名表。"""
    with get_conn() as conn:
        rows = list(conn.execute("SELECT ingredient, allergen FROM ingredient_map"))
        allergen_rows = list(conn.execute("SELECT name, aliases FROM allergens"))
    direct: dict[str, str] = {}
    for r in rows:
        if r["allergen"]:
            direct[r["ingredient"].strip()] = r["allergen"].strip()
    aliases: dict[str, str] = {}
    canonical: set[str] = set()
    for r in allergen_rows:
        canonical.add(r["name"])
        aliases[r["name"]] = r["name"]
        for a in (r["aliases"] or "").split(","):
            a = a.strip()
            if a:
                aliases[a] = r["name"]
    return {"direct": direct, "aliases": aliases, "canonical": canonical}
