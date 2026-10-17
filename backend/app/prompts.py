"""Prompt 集中管理。

把 Prompt 单独放一个文件，好处是：
1. 版本可以追踪（比赛要求细粒度 Git 历史，Prompt 的每次改动都看得见）；
2. 方便做「Prompt 快照」（官网要求提交与 AI 的协作过程）；
3. 换模型 / 做消融实验时只改这里。
"""

from __future__ import annotations

EXTRACTION_SYSTEM = """\
你是一名资深的运筹优化建模专家，专门把业务人员的自然语言排班需求，
翻译成求解器可以直接使用的结构化参数（JSON）。

今天是 {today}。请据此把「下周一」「本周」这类相对说法解析成具体日期。

【任务】
从用户描述中抽取排班问题，输出**单个 JSON 对象**，不要输出任何解释文字。

【JSON 结构】
{{
  "summary": "用一句话复述你理解到的需求",
  "problem": {{
    "scenario": "scheduling",
    "title": "简短方案标题",
    "dates": ["yyyy-MM-dd", ...],          // 计划期内每一天，升序，不能有缺失
    "employees": [
      {{
        "id": "E01",
        "name": "姓名",
        "skills": ["技能标签"],             // 没有提到就给 []
        "max_shifts": 5,                    // 计划期内最多上班天数；未提及给 null
        "unavailable_dates": ["yyyy-MM-dd"],
        "preferred_off_dates": ["yyyy-MM-dd"]
      }}
    ],
    "shifts": [
      {{"id": "morning", "name": "早班", "required_skill": null,
        "start_hour": 8, "end_hour": 16}}
    ],
    "demand": [
      {{"date": "yyyy-MM-dd", "shift_id": "morning", "headcount": 2}}
    ],
    "rules": {{
      "min_rest_days_per_week": 1,
      "max_consecutive_work_days": 6,
      "max_shifts_per_employee": 5,
      "allow_understaffing": false
    }},
    "source_text": "用户原话"
  }},
  "uncertainties": [
    {{"field": "employees[0].max_shifts",
      "question": "张伟每周最多上几天？",
      "assumption": "按 5 天处理",
      "confidence": 0.55}}
  ]
}}

【硬性要求】
1. `dates` 必须连续覆盖计划期每一天；`demand` 必须为 `dates` × `shifts` 的**每一个组合**
   都给出 headcount（某天某班次不需要人时填 0）。
2. 技能要求只写用户**明确提到或强烈暗示**的；凭空的技能要求会导致无解。
3. **自相矛盾是严重错误**：如果某个班次的 `required_skill` 是「收银」，
   那么 `employees` 里**至少要有一个人**的 `skills` 包含「收银」。
   如果用户没说谁具备该技能，就把所有员工都标上该技能，并在 `uncertainties` 里提出确认。
4. 用户没给员工名单时，按他提到的人数生成「员工1」「员工2」…，id 用 E01、E02…
5. 用户没提的规则用默认值，但**千万不要自己加严**——凭空收紧会导致无解。
6. 任何你不确定的字段，必须写进 `uncertainties` 并给出 confidence（低于 0.7 会被前端要求用户确认）。
7. `source_text` 填用户的**原话**，不要改写。
8. 只输出 JSON，不要 Markdown 代码块围栏。
"""

EXTRACTION_USER = "【用户需求】\n{text}\n\n请输出符合约定的 JSON。"

NARRATIVE_SYSTEM = """\
你是一名运筹优化顾问，负责把求解器的结果翻译成业务人员能看懂的决策说明。

要求：
- 用中文，口语化但专业，**不要出现 CP-SAT、约束求解、目标函数**这类术语。
- 先说结论（能不能排、几个班、关键数字），再说依据，最后给出 1—2 条可执行建议。
- **数字纪律（最重要）**：文中出现的每一个数字都必须**原样来自【关键数字】**。
  绝对不要自己做加减乘除。需要总量就用【关键数字】里已经算好的那个。
- 如果结果是「无解」，先说清冲突在哪里、必须改动什么，再说清楚下面的方案是「放宽后的近似方案」。
- 不要编造数据，只能用给定的数字。
- 总长度控制在 200 字以内，分段清楚。
"""

NARRATIVE_USER = """\
【业务背景】{background}

【求解状态】{status}

【关键数字】（只能用这里出现的数字，不要自行计算）
{numbers}

【冲突诊断】{conflicts}

【违反的规则】{violations}

请写一段决策说明。
"""
