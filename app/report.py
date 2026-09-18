"""标签审查报告导出（Markdown / 纯文本）。"""
from __future__ import annotations

from datetime import datetime, timezone

from .review import CompareResult, ReviewResult


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _tree_lines(result: ReviewResult) -> list[str]:
    lines = []
    for e in result.flat:
        indent = "  " * e.depth
        tag = "复合" if e.is_composite else "原料"
        if e.direct:
            lines.append(f"{indent}- [{tag}] {e.name} | 直接命中：{'、'.join(e.direct)}")
        else:
            lines.append(f"{indent}- [{tag}] {e.name}")
        # 仅复合原料展示子配料向上传播的部分
        if e.is_composite:
            propagated_only = [a for a in e.propagated if a not in e.direct]
            if propagated_only:
                lines.append(f"{indent}    └ 子配料传播：{'、'.join(propagated_only)}")
    return lines


def _verdict(result: ReviewResult) -> str:
    if result.label_ok:
        return "合格：标签声明与配方过敏原一致，无遗漏、无多报。"
    problems = []
    if result.balance_error:
        problems.append(f"配方括号错误（{result.balance_error}）")
    if result.missing:
        problems.append(f"漏标 {len(result.missing)} 项：{'、'.join(result.missing)}")
    if result.extra:
        problems.append(f"多报 {len(result.extra)} 项：{'、'.join(result.extra)}")
    if result.unrecognized:
        problems.append(f"无法识别的声明 {len(result.unrecognized)} 项：{'、'.join(result.unrecognized)}")
    return "不合格：" + "；".join(problems) + "。"


def build_single_report(result: ReviewResult, recipe_name: str, version: str,
                        ingredients_text: str, declared_text: str,
                        fmt: str = "md") -> str:
    md = fmt == "md"
    h = (lambda s: f"# {s}\n") if md else (lambda s: f"===== {s} =====\n")
    h2 = (lambda s: f"## {s}\n") if md else (lambda s: f"--- {s} ---\n")
    L: list[str] = []
    L.append(h("食品过敏原标签审查报告"))
    L.append(f"- 产品名称：{recipe_name or '（未命名）'}")
    L.append(f"- 版本：{version or '—'}")
    L.append(f"- 生成时间：{_now()}")
    L.append("")

    L.append(h2("一、输入"))
    L.append("配方文本：")
    L.append("```")
    L.append(ingredients_text.strip())
    L.append("```")
    L.append("")
    L.append("标签声明：")
    L.append("```")
    L.append(declared_text.strip() or "（空）")
    L.append("```")
    L.append("")

    L.append(h2("二、配料层级与过敏原传播"))
    L.extend(_tree_lines(result))
    L.append("")

    L.append(h2("三、配方检出过敏原（按类别）"))
    if result.all_detected:
        for a in result.all_detected:
            sources = result.leaves.get(a, [])
            L.append(f"- {a}：{'、'.join(sources) if sources else '（复合配料传播）'}")
    else:
        L.append("- 未检出任何字典中的过敏原")
    L.append("")

    L.append(h2("四、标签核对"))
    L.append(f"标签声明片段：{'、'.join(result.declared) if result.declared else '（空）'}")
    if result.recognized_declared:
        mapped = "；".join(f"{t} → {c}" for t, c in result.recognized_declared.items())
        L.append(f"识别结果：{mapped}")
    L.append(f"- 漏标（配方含、标签未声明）：{'、'.join(result.missing) if result.missing else '无'}")
    L.append(f"- 多报（标签声明、配方不含）：{'、'.join(result.extra) if result.extra else '无'}")
    L.append(f"- 无法识别的声明片段：{'、'.join(result.unrecognized) if result.unrecognized else '无'}")
    if result.balance_error:
        L.append(f"- 语法错误：{result.balance_error}")
    L.append("")

    L.append(h2("五、结论"))
    L.append(_verdict(result))
    L.append("")
    return "\n".join(L)


def build_compare_report(cmp: CompareResult, name: str,
                         old_text: str, old_declared: str,
                         new_text: str, new_declared: str,
                         old_version: str = "", new_version: str = "",
                         fmt: str = "md") -> str:
    md = fmt == "md"
    h = (lambda s: f"# {s}\n") if md else (lambda s: f"===== {s} =====\n")
    h2 = (lambda s: f"## {s}\n") if md else (lambda s: f"--- {s} ---\n")
    L: list[str] = []
    L.append(h("配方过敏原变化对比报告"))
    L.append(f"- 产品名称：{name or '（未命名）'}")
    L.append(f"- 旧版本：{old_version or '—'}　新版本：{new_version or '—'}")
    L.append(f"- 生成时间：{_now()}")
    L.append("")

    status_text = {
        "unchanged": "两版配方过敏原无变化",
        "changed": "两版配方存在变化",
        "fixed": "变化后标签问题已修复",
        "broke": "变化引入了新的过敏原风险",
    }[cmp.status]
    L.append(h2("一、结论"))
    L.append(status_text)
    L.append(f"- 新增过敏原：{'、'.join(cmp.added_allergens) if cmp.added_allergens else '无'}")
    L.append(f"- 移除过敏原：{'、'.join(cmp.removed_allergens) if cmp.removed_allergens else '无'}")
    L.append(f"- 持续存在：{'、'.join(cmp.unchanged_allergens) if cmp.unchanged_allergens else '无'}")
    L.append("")

    L.append(h2("二、原料增减"))
    L.append(f"- 新增原料：{'、'.join(cmp.added_leaf_names) if cmp.added_leaf_names else '无'}")
    L.append(f"- 删除原料：{'、'.join(cmp.removed_leaf_names) if cmp.removed_leaf_names else '无'}")
    L.append("")

    L.append(h2("三、按过敏原类别的原料变化"))
    any_detail = False
    for a in cmp.added_allergens + cmp.unchanged_allergens:
        if a in cmp.added_leaves:
            L.append(f"- {a} 新增来源：{'、'.join(cmp.added_leaves[a])}")
            any_detail = True
    for a in cmp.removed_allergens + cmp.unchanged_allergens:
        if a in cmp.removed_leaves:
            L.append(f"- {a} 移除来源：{'、'.join(cmp.removed_leaves[a])}")
            any_detail = True
    if not any_detail:
        L.append("- 无类别内原料变化")
    L.append("")

    L.append(h2("四、两版标签核对"))
    for tag, r, ver in (("旧版", cmp.old, old_version), ("新版", cmp.new, new_version)):
        L.append(f"[{tag} {ver or ''}] {_verdict(r)}")
        if r.missing:
            L.append(f"  漏标：{'、'.join(r.missing)}")
        if r.extra:
            L.append(f"  多报：{'、'.join(r.extra)}")
        if r.unrecognized:
            L.append(f"  未识别：{'、'.join(r.unrecognized)}")
    L.append("")

    L.append(h2("五、原始配方"))
    L.append("旧版配方：")
    L.append("```")
    L.append(old_text.strip())
    L.append("```")
    L.append(f"旧版声明：{old_declared.strip() or '（空）'}")
    L.append("")
    L.append("新版配方：")
    L.append("```")
    L.append(new_text.strip())
    L.append("```")
    L.append(f"新版声明：{new_declared.strip() or '（空）'}")
    L.append("")
    return "\n".join(L)
