"""运筹决策 Agent —— 领域数据模型 (Pydantic v2)。

这些模型同时承担两个职责：
1. 后端内部的数据结构；
2. 约束 LLM 输出的 JSON Schema —— 让「自然语言 → 数学模型」这一步可校验、可拦截幻觉。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 排班场景
# --------------------------------------------------------------------------- #


class Employee(BaseModel):
    """一名可排班的员工。"""

    id: str = Field(description="员工唯一标识，如 E01")
    name: str = Field(description="姓名")
    skills: list[str] = Field(default_factory=list, description="具备的技能标签，如 ['收银','理货']")
    max_shifts: int | None = Field(
        default=None,
        description="计划期内最多上班天数；None 表示使用全局规则 max_shifts_per_employee",
    )
    unavailable_dates: list[str] = Field(
        default_factory=list, description="不可上班的日期（ISO 格式 yyyy-MM-dd）"
    )
    preferred_off_dates: list[str] = Field(
        default_factory=list, description="希望休息的日期，属软约束"
    )


class ShiftType(BaseModel):
    """一种班次。"""

    id: str = Field(description="班次标识，如 morning")
    name: str = Field(description="班次名称，如 早班")
    required_skill: str | None = Field(
        default=None, description="该班次要求的技能；None 表示不限"
    )
    start_hour: int = Field(default=0, ge=0, le=24, description="开始小时")
    end_hour: int = Field(default=8, ge=0, le=24, description="结束小时")


class DemandEntry(BaseModel):
    """某天某班次需要多少人。"""

    date: str = Field(description="日期，ISO 格式 yyyy-MM-dd")
    shift_id: str = Field(description="班次 id")
    headcount: int = Field(ge=0, description="需求人数")


class SchedulingRules(BaseModel):
    """全局排班规则。"""

    min_rest_days_per_week: int = Field(
        default=1, ge=0, description="每 7 天窗口内最少休息天数"
    )
    max_consecutive_work_days: int = Field(
        default=6, ge=1, description="最多连续工作天数"
    )
    max_shifts_per_employee: int = Field(
        default=5, ge=0, description="计划期内每人最多上班天数（员工未单独指定时生效）"
    )
    allow_understaffing: bool = Field(
        default=False, description="是否允许人数不足（通常为 False，触发自动诊断）"
    )


class SchedulingProblem(BaseModel):
    """一个完整的排班问题实例 —— 即「数学模型」的参数部分。"""

    scenario: Literal["scheduling"] = "scheduling"
    title: str = Field(default="排班方案", description="方案标题")
    dates: list[str] = Field(description="计划期内的日期列表，ISO 格式，按时间升序")
    employees: list[Employee] = Field(description="参与排班的员工")
    shifts: list[ShiftType] = Field(description="班次类型")
    demand: list[DemandEntry] = Field(description="各班次每日人数需求")
    rules: SchedulingRules = Field(default_factory=SchedulingRules)
    source_text: str = Field(default="", description="用户原始自然语言描述（保留用于追溯）")


# --------------------------------------------------------------------------- #
# LLM 抽取层：自然语言 → 问题实例
# --------------------------------------------------------------------------- #


class Uncertainty(BaseModel):
    """LLM 抽取过程中不确定、需要用户确认的字段（人在环）。"""

    field: str = Field(description="字段路径，如 employees[2].max_shifts")
    question: str = Field(description="向用户提出的确认问题")
    assumption: str = Field(description="当前采用的默认假设")
    confidence: float = Field(ge=0.0, le=1.0, description="该字段的置信度")


class ExtractionResult(BaseModel):
    """LLM 一次抽取的完整产物。"""

    problem: SchedulingProblem
    summary: str = Field(default="", description="用一句话复述理解到的需求")
    uncertainties: list[Uncertainty] = Field(
        default_factory=list, description="低于置信度阈值、需要用户确认的字段"
    )


# --------------------------------------------------------------------------- #
# 求解结果
# --------------------------------------------------------------------------- #


class Assignment(BaseModel):
    date: str
    shift_id: str
    shift_name: str
    employee_id: str
    employee_name: str


class Conflict(BaseModel):
    """一条被诊断为冲突的规则。"""

    rule: str = Field(description="规则键，见 solver.RULE_LABELS")
    label: str = Field(description="规则的中文说明")
    detail: str = Field(description="该规则的具体实例，如『10-21 的 早班 需要 3 人，可用仅 2 人』")


class SolveResult(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    assignments: list[Assignment] = Field(default_factory=list)
    objective: float | None = None
    solve_time_seconds: float = 0.0
    conflicts: list[Conflict] = Field(default_factory=list)
    violations: list[str] = Field(
        default_factory=list, description="松弛后仍被违反的规则（自动自愈的产物）"
    )
    diagnosis: str = Field(default="", description="面向用户的中文诊断说明")
    stats: dict[str, float | int | str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Agent 运行轨迹（agent 与 what-if 共用）
# --------------------------------------------------------------------------- #


class AgentStep(BaseModel):
    """Agent 的一步，供前端展示 trace。"""

    key: str
    name: str
    status: Literal["pending", "running", "ok", "warn", "error"] = "pending"
    detail: str = ""
    duration_ms: int = 0


# --------------------------------------------------------------------------- #
# What-if：自然语言改约束 → 重解 → 方案对比
# --------------------------------------------------------------------------- #


class WhatIfOp(BaseModel):
    """一条结构化的变更操作。

    刻意做成「受限操作集」而不是「让模型改整个问题」——
    整份改写容易改坏用户没提到的地方，受限操作集可以逐条审计、可解释。
    """

    op: Literal[
        "set_unavailable",        # 让某人在指定日期不可上班
        "clear_unavailable",      # 清除某人的不可用日期
        "set_max_shifts",         # 改某人的上班天数上限
        "set_global_max_shifts",  # 改全局上班天数上限
        "set_min_rest_days",      # 改每周最少休息天数
        "set_max_consecutive",    # 改连续工作天数上限
        "set_demand",             # 改某班次人数需求（date 为空表示所有日期）
        "add_skill",              # 给某人加一个技能
        "remove_employee",        # 移除某人
    ] = Field(description="操作类型")
    employee_id: str | None = Field(default=None, description="员工 id，如 E01")
    employee_name: str | None = Field(default=None, description="员工姓名（便于人读）")
    skill: str | None = Field(default=None, description="技能标签")
    date: str | None = Field(default=None, description="单个日期 yyyy-MM-dd；为空表示全部")
    dates: list[str] = Field(default_factory=list, description="多个日期")
    shift_id: str | None = Field(default=None, description="班次 id")
    headcount: int | None = Field(default=None, description="人数")
    value: int | None = Field(default=None, description="通用整数参数")
    reason: str = Field(default="", description="为什么要做这个变更（人话）")


class WhatIfPatch(BaseModel):
    """LLM 对一句「如果……会怎样」的结构化理解。"""

    understood: str = Field(default="", description="用一句话复述用户想要的变更")
    ops: list[WhatIfOp] = Field(default_factory=list, description="要施加的变更操作")


class EmployeeDelta(BaseModel):
    employee_id: str
    name: str
    before: int
    after: int
    delta: int


class ScheduleDiff(BaseModel):
    """两个排班方案之间的差异。"""

    added: list[Assignment] = Field(default_factory=list, description="新增的排班")
    removed: list[Assignment] = Field(default_factory=list, description="被取消的排班")
    unchanged_count: int = 0
    per_employee: list[EmployeeDelta] = Field(default_factory=list)
    status_before: str = ""
    status_after: str = ""
    total_before: int = 0
    total_after: int = 0
    objective_before: float | None = None
    objective_after: float | None = None
    max_shift_delta: int = 0


class WhatIfResult(BaseModel):
    ok: bool = False
    understood: str = ""
    ops: list[WhatIfOp] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="补丁应用日志")
    patched_problem: SchedulingProblem | None = None
    baseline_result: SolveResult | None = None
    result: SolveResult | None = None
    diff: ScheduleDiff | None = None
    narrative: str = ""
    steps: list[AgentStep] = Field(default_factory=list)
    error: str = ""
