"""配方文本解析与过敏原审查核心。

配方文本约定（兼容中英文/全角半角符号）：
- 原料之间用逗号、顿号或分号分隔；
- 复合原料用括号展开子配料，例如：
  小麦粉、白砂糖、人造奶油（植物油、水、大豆磷脂、牛奶、食用盐）、鸡蛋

解析为嵌套树后，过敏原按层级向上传播：
父节点的过敏原集合 = 自身直接命中的过敏原 ∪ 所有后代命中的过敏原。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OPEN = "("
CLOSE = ")"
SEPARATORS = ",;，；、"


# ---------------------------------------------------------------- 文本归一化

def normalize_text(text: str) -> str:
    """全角括号/分隔符归一，并去除空白。"""
    table = {
        "（": OPEN, "）": CLOSE,
        "【": OPEN, "】": CLOSE,
        "［": OPEN, "］": CLOSE,
        "，": ",", "；": ";", "、": ",",
        "　": "", " ": "", "\t": "", "\n": "", "\r": "",
    }
    out = []
    for ch in text:
        out.append(table.get(ch, ch))
    return "".join(out)


# ---------------------------------------------------------------- 解析树

@dataclass
class Node:
    name: str
    children: list["Node"] = field(default_factory=list)
    # 审查阶段填充
    direct_allergens: set[str] = field(default_factory=set)
    all_allergens: set[str] = field(default_factory=set)
    direct_matched: dict[str, str] = field(default_factory=dict)
    # 过敏原类别 -> 命中该类别的后代原料名（仅用于溯源展示）
    child_sources: dict[str, list[str]] = field(default_factory=dict)


class ParseError(ValueError):
    pass


def parse(text: str) -> list[Node]:
    """解析配方文本，返回根级原料节点列表。"""
    s = normalize_text(text)
    nodes: list[Node] = []
    i = 0
    n = len(s)

    def parse_until(stop: str | None) -> tuple[list[Node], int]:
        nonlocal i
        result: list[Node] = []
        buf = ""
        while i < n:
            ch = s[i]
            if stop is not None and ch == stop:
                i += 1
                if buf.strip():
                    result.append(Node(name=buf.strip()))
                return result, i
            if ch in SEPARATORS:
                if buf.strip():
                    result.append(Node(name=buf.strip()))
                buf = ""
                i += 1
            elif ch == OPEN:
                # buf 里是复合原料名；归一化文本里不会有空格，直接 strip
                parent_name = buf.strip()
                i += 1
                children, i = parse_until(CLOSE)
                if parent_name:
                    parent = Node(name=parent_name, children=children)
                    result.append(parent)
                else:
                    # 没有名字的括号组：把子项提上来
                    result.extend(children)
                buf = ""
            else:
                buf += ch
                i += 1
        if stop is not None:
            raise ParseError(f"括号不配对：缺少右括号 {stop}")
        if buf.strip():
            result.append(Node(name=buf.strip()))
        return result, i

    nodes, _ = parse_until(None)
    return nodes


def check_balance(text: str) -> str | None:
    """返回括号错误描述，无错误返回 None。"""
    s = normalize_text(text)
    depth = 0
    for ch in s:
        if ch == OPEN:
            depth += 1
        elif ch == CLOSE:
            depth -= 1
            if depth < 0:
                return "存在多余的右括号“）”"
    if depth > 0:
        return f"括号不配对：缺少 {depth} 个右括号“）”"
    return None


# ---------------------------------------------------------------- 声明文本解析

def parse_declared(text: str) -> list[str]:
    """解析标签上声明的过敏原文本，按常见分隔符切分。

    注意：不能用 str.strip(字符集) 去除“含有”等字样——strip 是按字符集合
    逐个削除，会把“有乳”中的“乳”误删。这里只做标点切分与空白清理，
    “含有/本产品”等引导词在类别匹配阶段用包含匹配消化。
    """
    raw = (text or "").replace("，", ",").replace("、", ",").replace("；", ";")
    raw = raw.replace("。", ",").replace("\n", ",").replace("/", ",")
    tokens = []
    prefixes = ("本产品可能含有", "本产品含", "可能含有", "含有", "含", "过敏原", "致敏物")
    for part in raw.replace(";", ",").split(","):
        t = part.strip().strip("：:").strip()
        t = t.strip("()（）【】[] ").strip()
        changed = True
        while t and changed:
            changed = False
            for p in prefixes:
                if t.startswith(p) and len(t) > len(p):
                    t = t[len(p):].strip("：: ").strip()
                    changed = True
                    break
        if t:
            tokens.append(t)
    return tokens


# ---------------------------------------------------------------- 过敏原审查

@dataclass
class FlatEntry:
    depth: int
    name: str
    direct: list[str]
    propagated: list[str]
    is_composite: bool
    sources: dict[str, list[str]]


@dataclass
class ReviewResult:
    tree: list[Node]
    flat: list[FlatEntry]
    leaves: dict[str, list[str]]          # 规范过敏原 -> 命中的叶子原料名
    all_detected: list[str]
    declared: list[str]
    recognized_declared: dict[str, str]   # 声明片段 -> 规范过敏原
    missing: list[str]                    # 漏标
    extra: list[str]                      # 多报（已识别但配方不含）
    unrecognized: list[str]               # 无法识别的声明片段
    leaf_names: list[str]                 # 全部叶子原料（归一化名）
    label_ok: bool
    balance_error: str | None = None


def _match_direct(name: str, direct_map: dict[str, str]) -> dict[str, str]:
    """原料名命中过敏原字典：精确优先，其次最长子串（键至少 2 字）。

    返回 {过敏原类别: 命中的字典键}，一个原料可能同时命中多类
    （如“小麦酱油”同时含小麦与大豆——实际字典中酱油只配麸质谷物）。
    """
    hits: dict[str, str] = {}
    if name in direct_map:
        hits[direct_map[name]] = name
        return hits
    best_len = 0
    for key, allergen in direct_map.items():
        if len(key) >= 2 and key in name and len(key) > best_len:
            # 最长匹配胜出；同长度时保留先出现的类别
            hits = {allergen: key}
            best_len = len(key)
        elif len(key) >= 2 and key in name and len(key) == best_len:
            hits[allergen] = key
    return hits


def review(ingredients_text: str, declared_text: str, knowledge: dict[str, Any]) -> ReviewResult:
    balance_error = check_balance(ingredients_text)
    # 括号不配对时无法可靠建树，仅做声明侧核对并给出错误提示
    tree = [] if balance_error else parse(ingredients_text)
    direct_map = knowledge["direct"]
    aliases = knowledge["aliases"]

    flat: list[FlatEntry] = []
    leaves: dict[str, list[str]] = {}
    leaf_names: list[str] = []

    def walk(node: Node, depth: int) -> set[str]:
        matched = _match_direct(node.name, direct_map)
        node.direct_matched = matched
        direct_set = set(matched.keys())
        node.direct_allergens = direct_set

        # 先占位，保证平铺结果是前序（父在子之前），传播集合稍后回填
        entry = FlatEntry(
            depth=depth, name=node.name, direct=sorted(direct_set),
            propagated=[], is_composite=bool(node.children), sources={},
        )
        flat.append(entry)

        child_all: set[str] = set()
        sources: dict[str, list[str]] = {}
        for child in node.children:
            child_all |= walk(child, depth + 1)
        # 溯源：子树中某过敏原来自哪些（直接命中的）节点
        for child in node.children:
            for allergen in child.direct_matched:
                sources.setdefault(allergen, []).append(child.name)
            for allergen, names in child.child_sources.items():
                sources.setdefault(allergen, []).extend(names)

        all_set = direct_set | child_all
        node.all_allergens = all_set
        node.child_sources = sources
        entry.propagated = sorted(all_set)
        entry.sources = {k: sorted(set(v)) for k, v in sources.items()}
        if not node.children:
            leaf_names.append(node.name)
            for a in all_set:
                leaves.setdefault(a, []).append(node.name)
        return all_set

    root_detected: set[str] = set()
    for root in tree:
        root_detected |= walk(root, 0)

    for a in leaves:
        leaves[a] = sorted(set(leaves[a]))

    # 标签声明 -> 规范过敏原
    declared = parse_declared(declared_text)
    recognized: dict[str, str] = {}
    unrecognized: list[str] = []
    for token in declared:
        canon = aliases.get(token)
        if canon is None:
            # 片段中包含某个别名（如“含有乳制品”）
            for alias, target in aliases.items():
                if len(alias) >= 2 and alias in token:
                    canon = target
                    break
        if canon is None:
            unrecognized.append(token)
        else:
            recognized[token] = canon

    declared_set = set(recognized.values())
    all_detected = sorted(root_detected)
    missing = sorted(root_detected - declared_set)
    extra = sorted(declared_set - root_detected)

    return ReviewResult(
        tree=tree,
        flat=flat,
        leaves=leaves,
        all_detected=all_detected,
        declared=declared,
        recognized_declared=recognized,
        missing=missing,
        extra=extra,
        unrecognized=sorted(set(unrecognized)),
        leaf_names=sorted(set(leaf_names)),
        label_ok=not missing and not extra and not unrecognized and not balance_error,
        balance_error=balance_error,
    )


# ---------------------------------------------------------------- 两版对比

@dataclass
class CompareResult:
    old: ReviewResult
    new: ReviewResult
    added_allergens: list[str]
    removed_allergens: list[str]
    unchanged_allergens: list[str]
    added_leaves: dict[str, list[str]]    # 过敏原 -> 新增叶子原料
    removed_leaves: dict[str, list[str]]
    added_leaf_names: list[str]
    removed_leaf_names: list[str]
    status: str                           # changed / unchanged / fixed / broke


def compare(old: ReviewResult, new: ReviewResult) -> CompareResult:
    old_set, new_set = set(old.all_detected), set(new.all_detected)
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    unchanged = sorted(old_set & new_set)

    added_leaves: dict[str, list[str]] = {}
    removed_leaves: dict[str, list[str]] = {}
    for a in added:
        added_leaves[a] = new.leaves.get(a, [])
    for a in removed:
        removed_leaves[a] = old.leaves.get(a, [])
    # 共同过敏原里叶子原料的增减
    for a in unchanged:
        ol, nl = set(old.leaves.get(a, [])), set(new.leaves.get(a, []))
        if nl - ol:
            added_leaves[a] = sorted(nl - ol)
        if ol - nl:
            removed_leaves[a] = sorted(ol - nl)

    if not added and not removed and not (set(old.leaf_names) ^ set(new.leaf_names)):
        status = "unchanged"
    elif not added and old.missing and not new.missing:
        status = "fixed"
    elif added and not old.missing:
        status = "broke"
    else:
        status = "changed"

    return CompareResult(
        old=old,
        new=new,
        added_allergens=added,
        removed_allergens=removed,
        unchanged_allergens=unchanged,
        added_leaves=added_leaves,
        removed_leaves=removed_leaves,
        added_leaf_names=sorted(set(new.leaf_names) - set(old.leaf_names)),
        removed_leaf_names=sorted(set(old.leaf_names) - set(new.leaf_names)),
        status=status,
    )
