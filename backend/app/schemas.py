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
