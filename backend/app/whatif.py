"""What-if：把一句「如果……会怎样」变成**可审计的补丁**，再重解、再对比。

设计取舍
--------
为什么不让大模型直接改整份问题 JSON？
因为那样模型很容易顺手改坏用户根本没提到的地方，而且出了问题无法定位。
这里改成**受限操作集**：模型只能输出 `set_unavailable` / `set_demand` 这类明确动作，
每一条都能被人读懂、被程序校验、被日志记录 —— 这也正是答辩时"为什么可信"的答案。
"""

from __future__ import annotations

from app.schemas import (
    Assignment,
    DemandEntry,
    EmployeeDelta,
    ScheduleDiff,
    SchedulingProblem,
    SolveResult,
    WhatIfOp,
)


def _assignment_key(a: Assignment) -> tuple[str, str, str]:
    return (a.employee_id, a.date, a.shift_id)


def _resolve_employee(problem: SchedulingProblem, op: WhatIfOp):
    """按 id 或姓名找到员工，姓名支持包含匹配（LLM 常写成「张伟」而 id 是 E01）。"""
    by_id = {e.id: e for e in problem.employees}
    if op.employee_id and op.employee_id in by_id:
        return by_id[op.employee_id]
    if op.employee_name:
        for e in problem.employees:
            if e.name == op.employee_name:
                return e
        for e in problem.employees:
            if op.employee_name in e.name or e.name in op.employee_name:
                return e
    return None


def apply_patch(
    problem: SchedulingProblem, ops: list[WhatIfOp]
) -> tuple[SchedulingProblem, list[str]]:
    """把操作集施加到问题上，返回新问题与逐条执行日志（不修改入参）。"""
    patched = problem.model_copy(deep=True)
    notes: list[str] = []
    date_set = set(patched.dates)
    shift_ids = {s.id for s in patched.shifts}

    for i, op in enumerate(ops, 1):
        tag = f"[{i}]"
        kind = op.op

        if kind in ("set_unavailable", "clear_unavailable"):
            emp = _resolve_employee(patched, op)
            if emp is None:
                notes.append(f"{tag} 跳过：找不到员工 {op.employee_id or op.employee_name}")
                continue
            targets = op.dates or ([op.date] if op.date else [])
            valid = [d for d in targets if d in date_set]
            if kind == "set_unavailable":
                if not valid:
                    notes.append(f"{tag} 跳过：{emp.name} 的变更没有有效日期")
                    continue
                emp.unavailable_dates = sorted(set(emp.unavailable_dates) | set(valid))
                notes.append(f"{tag} {emp.name} 在 {'、'.join(valid)} 不可上班")
            else:
                if valid:
                    emp.unavailable_dates = sorted(set(emp.unavailable_dates) - set(valid))
                    notes.append(f"{tag} {emp.name} 恢复 {'、'.join(valid)} 可上班")
                else:
                    emp.unavailable_dates = []
                    notes.append(f"{tag} {emp.name} 的不可用日期已全部清除")

        elif kind == "set_max_shifts":
            emp = _resolve_employee(patched, op)
            if emp is None or op.value is None:
                notes.append(f"{tag} 跳过：员工或数值无效")
                continue
            emp.max_shifts = max(0, min(op.value, len(patched.dates)))
            notes.append(f"{tag} {emp.name} 上班天数上限改为 {emp.max_shifts} 天")

        elif kind == "set_global_max_shifts":
            if op.value is None:
                notes.append(f"{tag} 跳过：没有给出数值")
                continue
            patched.rules.max_shifts_per_employee = max(0, op.value)
            notes.append(f"{tag} 全局上班天数上限改为 {op.value} 天")

        elif kind == "set_min_rest_days":
            if op.value is None:
                notes.append(f"{tag} 跳过：没有给出数值")
                continue
            patched.rules.min_rest_days_per_week = max(0, min(7, op.value))
            notes.append(f"{tag} 每周最少休息天数改为 {patched.rules.min_rest_days_per_week} 天")

        elif kind == "set_max_consecutive":
            if op.value is None:
                notes.append(f"{tag} 跳过：没有给出数值")
                continue
            patched.rules.max_consecutive_work_days = max(1, op.value)
            notes.append(f"{tag} 连续工作天数上限改为 {op.value} 天")

        elif kind == "set_demand":
            if not op.shift_id or op.shift_id not in shift_ids:
                notes.append(f"{tag} 跳过：班次「{op.shift_id}」无效")
                continue
            if op.headcount is None:
                notes.append(f"{tag} 跳过：没有给出人数")
                continue
            targets = [op.date] if op.date else list(patched.dates)
            targets = [d for d in targets if d in date_set]
            if not targets:
                notes.append(f"{tag} 跳过：没有有效日期")
                continue
            touched = 0
            for d in targets:
                hit = False
                for entry in patched.demand:
                    if entry.date == d and entry.shift_id == op.shift_id:
                        entry.headcount = op.headcount
                        hit = True
                        touched += 1
                if not hit:
                    patched.demand.append(
                        DemandEntry(date=d, shift_id=op.shift_id, headcount=op.headcount)
                    )
                    touched += 1
            scope = op.date or "全部日期"
            notes.append(f"{tag} 班次「{op.shift_id}」{scope} 人数改为 {op.headcount}（{touched} 条）")

        elif kind == "add_skill":
            emp = _resolve_employee(patched, op)
            if emp is None or not op.skill:
                notes.append(f"{tag} 跳过：员工或技能无效")
                continue
            if op.skill not in emp.skills:
                emp.skills.append(op.skill)
            notes.append(f"{tag} {emp.name} 增加技能「{op.skill}」")

        elif kind == "remove_employee":
            emp = _resolve_employee(patched, op)
            if emp is None:
                notes.append(f"{tag} 跳过：找不到员工")
                continue
            patched.employees = [e for e in patched.employees if e.id != emp.id]
            notes.append(f"{tag} 移除员工 {emp.name}（剩余 {len(patched.employees)} 人）")

        else:  # pragma: no cover - Literal 已限制取值范围
            notes.append(f"{tag} 跳过：不支持的操作 {kind}")

    return patched, notes


def compute_diff(
    before_problem: SchedulingProblem,
    before_result: SolveResult,
    after_problem: SchedulingProblem,
    after_result: SolveResult,
) -> ScheduleDiff:
    """对比两个排班方案：谁多了、谁少了、每个人工时怎么变。"""
    b_map = {_assignment_key(a): a for a in before_result.assignments}
    a_map = {_assignment_key(a): a for a in after_result.assignments}

    added = [a_map[k] for k in a_map if k not in b_map]
    removed = [b_map[k] for k in b_map if k not in a_map]
    unchanged = len(b_map.keys() & a_map.keys())

    before_count: dict[str, int] = {}
    for a in before_result.assignments:
        before_count[a.employee_id] = before_count.get(a.employee_id, 0) + 1
    after_count: dict[str, int] = {}
    for a in after_result.assignments:
        after_count[a.employee_id] = after_count.get(a.employee_id, 0) + 1

    names: dict[str, str] = {e.id: e.name for e in before_problem.employees}
    names.update({e.id: e.name for e in after_problem.employees})

    rows: list[EmployeeDelta] = []
    for eid in sorted(set(before_count) | set(after_count) | set(names)):
        b = before_count.get(eid, 0)
        a = after_count.get(eid, 0)
        rows.append(
            EmployeeDelta(
                employee_id=eid, name=names.get(eid, eid), before=b, after=a, delta=a - b
            )
        )
    rows.sort(key=lambda r: (-abs(r.delta), r.name))

    return ScheduleDiff(
        added=sorted(added, key=lambda x: (x.date, x.shift_id)),
        removed=sorted(removed, key=lambda x: (x.date, x.shift_id)),
        unchanged_count=unchanged,
        per_employee=rows,
        status_before=before_result.status,
        status_after=after_result.status,
        total_before=len(before_result.assignments),
        total_after=len(after_result.assignments),
        objective_before=before_result.objective,
        objective_after=after_result.objective,
        max_shift_delta=max((abs(r.delta) for r in rows), default=0),
    )
