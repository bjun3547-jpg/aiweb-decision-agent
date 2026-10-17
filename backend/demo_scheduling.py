"""离线演示：不需要 LLM，直接用 CPT-SAT 跑通两个排班场景。

    cd backend
    ..\\.venv\\Scripts\\python.exe demo_scheduling.py

场景 A：约束合理 → 求得最优排班
场景 B：约束互相冲突 → 触发「不可行自愈」，输出冲突诊断 + 违约最小的可行方案
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import (  # noqa: E402
    DemandEntry,
    Employee,
    SchedulingProblem,
    SchedulingRules,
    ShiftType,
)
from app.solver.scheduling import render_schedule_table, solve, validate_problem  # noqa: E402

DATES = [f"2026-10-{d:02d}" for d in range(19, 26)]  # 10/19 ~ 10/25

SHIFTS = [
    ShiftType(id="morning", name="早班", start_hour=8, end_hour=16),
    ShiftType(id="evening", name="晚班", required_skill="收银", start_hour=16, end_hour=24),
]

EMPLOYEES = [
    Employee(id="E01", name="张伟", skills=["收银", "理货"], unavailable_dates=["2026-10-21"]),
    Employee(id="E02", name="李娜", skills=["收银"], preferred_off_dates=["2026-10-24"]),
    Employee(id="E03", name="王强", skills=["理货"]),
    Employee(id="E04", name="刘洋", skills=["收银", "理货"]),
    Employee(id="E05", name="陈静", skills=["收银"]),
    Employee(id="E06", name="赵磊", skills=["理货"], preferred_off_dates=["2026-10-19"]),
    Employee(id="E07", name="孙悦", skills=["收银", "理货"]),
]


def _demand(per_shift: int) -> list[DemandEntry]:
    return [
        DemandEntry(date=d, shift_id=s.id, headcount=per_shift)
        for d in DATES
        for s in SHIFTS
    ]


def build_feasible() -> SchedulingProblem:
    return SchedulingProblem(
        title="便利店下周排班（约束合理）",
        dates=DATES,
        employees=EMPLOYEES,
        shifts=SHIFTS,
        demand=_demand(2),
        rules=SchedulingRules(
            min_rest_days_per_week=1,
            max_consecutive_work_days=5,
            max_shifts_per_employee=5,
        ),
        source_text="7 个店员，每天早班晚班各要 2 个人，每人每周最多上 5 天班，每周至少休 1 天。",
    )


def build_infeasible() -> SchedulingProblem:
    """把需求抬到 3 人/班 —— 7 人 × 每人最多 5 天 ≈ 34 人班 < 7×2×3 = 42 人班，必然无解。"""
    return SchedulingProblem(
        title="便利店下周排班（约束冲突）",
        dates=DATES,
        employees=EMPLOYEES,
        shifts=SHIFTS,
        demand=_demand(3),
        rules=SchedulingRules(
            min_rest_days_per_week=1,
            max_consecutive_work_days=6,
            max_shifts_per_employee=5,
        ),
        source_text="每天早班晚班各要 3 个人，每人每周最多上 5 天，每周至少休 1 天。",
    )


def run(problem: SchedulingProblem) -> None:
    print("=" * 78)
    print(f"【场景】{problem.title}")
    print(f"  原文：{problem.source_text}")
    print(f"  规模：{len(problem.dates)} 天 × {len(problem.shifts)} 班次 × "
          f"{len(problem.employees)} 员工，共 {len(problem.demand)} 条需求")

    warnings = validate_problem(problem)
    for w in warnings:
        print(f"  ⚠ 体检：{w}")

    result = solve(problem, time_limit=10.0)
    print(f"\n  求解状态：{result.status}　耗时 {result.solve_time_seconds:.3f}s")

    if result.conflicts:
        print("\n  —— 冲突诊断 ——")
        for c in result.conflicts:
            print(f"    × {c.label}：{c.detail}")
        print("\n  —— 面向用户的解释 ——")
        for line in result.diagnosis.splitlines():
            print("    " + line)

    if result.violations:
        print("\n  —— 松弛后仍违反的规则（违约最小方案）——")
        for v in result.violations:
            print(f"    ! {v}")

    if result.assignments:
        print("\n  —— 排班表 ——")
        table = render_schedule_table(problem, result)
        for line in table.splitlines():
            print("  " + line)

        per_emp: dict[str, int] = {}
        for a in result.assignments:
            per_emp[a.employee_name] = per_emp.get(a.employee_name, 0) + 1
        print("\n  人均上班天数：" + "，".join(f"{k} {v} 天" for k, v in sorted(per_emp.items())))
    print()


def main() -> None:
    run(build_feasible())
    run(build_infeasible())


if __name__ == "__main__":
    main()
