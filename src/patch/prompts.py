from __future__ import annotations

import json
from typing import Any

from src.patch.delta import delta_to_text


FEW_SHOT = """Quill Delta 编辑示例：
旧文本：支付重试上限为 5 次。
若需要把 5 改为 3，输出：
[
  { "retain": 8 },
  { "delete": 1 },
  { "insert": "3" }
]
若修改幅度大，直接全量替换（delete 的数值必须是旧文本总字符数，见下方标注）：
[
  { "delete": 12 },
  { "insert": "完整的新知识文本" }
]
请逐字核对 retain/delete 的数值，它们必须与真实字符位置严格一致；拿不准时优先使用全量替换。"""


HIGH_RISK_PROMPT = """你是资深架构师。模块 {unit_id} 是核心枢纽，被 {dependent_count} 个模块依赖。

旧知识：
{old_delta}

旧文本总字符数：{old_length}（如需全量替换，delete 使用该数值）

代码变更摘要：
{code_diff}

受影响文件：
{file_path}

{few_shot}

请生成 Quill Delta 操作数组，更新知识内容。必须包含：
1. 更新后的知识描述
2. 变更原因（业务或技术背景）
3. 若影响上下游模块，标注影响范围

输出要求：
- 仅输出 JSON 数组，格式为 Quill Delta 操作
- 禁止输出 Markdown、解释性自然语言或代码块
- 禁止使用“可能”“大概”等模糊词汇
- 保持内容与代码变更摘要一致，不引入无法从输入推导的新事实
- 知识文本中不得复制“代码变更摘要”“受影响文件”等输入片段的原文或标题，只能提炼其中事实
"""


LOW_RISK_PROMPT = """工具函数 {unit_id} 的参数、常量或实现事实发生变更。

旧知识：
{old_delta}

旧文本总字符数：{old_length}（如需全量替换，delete 使用该数值）

代码变更摘要：
{code_diff}

受影响文件：
{file_path}

{few_shot}

请仅更新该函数或模块的知识描述，补充新参数、常量或行为说明，不要发散业务含义。

输出要求：
- 仅输出 JSON 数组，格式为 Quill Delta 操作
- 禁止输出 Markdown、解释性自然语言或代码块
- 禁止使用“可能”“大概”等模糊词汇
- 保持内容与代码变更摘要一致，不引入无法从输入推导的新事实
- 知识文本中不得复制“代码变更摘要”“受影响文件”等输入片段的原文或标题，只能提炼其中事实
"""


def select_prompt_template(risk_level: str) -> str:
    if risk_level.upper() == "HIGH":
        return HIGH_RISK_PROMPT
    return LOW_RISK_PROMPT


def build_prompt(
    unit: dict[str, Any],
    code_diff: str,
    changed_files: list[str],
    dependent_count: int = 0,
) -> str:
    template = select_prompt_template(str(unit.get("risk_level", "LOW")))
    old_text = delta_to_text(unit.get("knowledge_delta"))
    return template.format(
        unit_id=unit["id"],
        dependent_count=dependent_count,
        old_delta=json.dumps(unit.get("knowledge_delta", {}), ensure_ascii=False, indent=2),
        old_length=len(old_text),
        code_diff=code_diff,
        file_path="\n".join(changed_files),
        few_shot=FEW_SHOT,
    )
