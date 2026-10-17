"""排班问题的 CP-SAT 建模、求解与不可行自愈。

这是项目的技术核心，分四段：

1. **硬约束求解** —— 五类业务规则各自作为一个「假设」(assumption) 字面量加入，
   因此无解时可以用 `SufficientAssumptionsForInfeasibility()` 定位到**规则类别**级别，
   而不是几十条零散约束（那样对业务人员毫无意义）。
2. **冲突诊断** —— 把求解器返回的抽象字面量翻译成人话，并附上
   「总需求量 vs 总承载能力」的算术解释 —— 这是用户真正能看懂的因果。
3. **自动松弛** —— 把规则转成带权重的软约束重解，得到「违约最小的可行方案」，
   连同「违反了哪几条、违反了多少」一起返回。
4. **可解释性** —— 把规则冲突与松弛代价翻译成业务建议。

对应方案文档里的创新点 ①「不可行自愈闭环」和 ④「求解器可解释性」。
"""

from __future__ import annotations

import math
import time
from typing import Any

from ortools.sat.python import cp_model

from app.schemas import (
    Assignment,
    Conflict,
    SchedulingProblem,
    SolveResult,
)

# --------------------------------------------------------------------------- #
# 规则元信息
# --------------------------------------------------------------------------- #

RULE_LABELS: dict[str, str] = {
    "demand": "各班次人数满足需求",
    "availability": "不安排员工在其不可用日期上班",
    "skill_match": "技能与班次要求匹配",
    "max_shifts": "个人上班天数不超过上限",
    "rest_days": "满足每 7 天最少休息天数",
    "max_consecutive": "连续工作天数不超过上限",
}

# 会被「松弛」的规则（skill_match 与「一天最多一班」是物理约束，永不放宽）
RELAXABLE_RULES = ("demand", "availability", "max_shifts", "rest_days", "max_consecutive")

# 松弛时的罚分权重：数值越大表示越不愿意违反
PENALTY: dict[str, int] = {
    "demand": 1000,
    "availability": 500,
    "max_shifts": 50,
    "rest_days": 40,
    "max_consecutive": 40,
}

# 冲突诊断后的中文建议
RULE_ADVICE: dict[str, str] = {
    "demand": "降低该班次的人数需求，或增加可用人手。",
    "availability": "该员工当天被标记为不可用，可改为允许其上班，或把需求转给别人。",
    "skill_match": "该班次要求的技能无人具备，需要给现有员工补技能，或降低技能要求。",
    "max_shifts": "上调个人上班天数上限，或增加人手分摊。",
    "rest_days": "下调最少休息天数，或增加可用人力。",
    "max_consecutive": "上调连续工作天数上限，或插入轮换人员。",
}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def _sliding_windows(items: list[str], size: int) -> list[list[str]]:
    if size <= 0 or size > len(items):
        return []
    return [items[i : i + size] for i in range(len(items) - size + 1)]


def effective_cap(employee_cap: int | None, global_cap: int, n_days: int, min_rest: int) -> int:
    """一名员工在计划期内实际最多能承担多少个班。

    取「个人上限」与「最少休息天数形成的上限」的较小值。
    """
    cap = employee_cap if employee_cap is not None else global_cap
    cap = max(0, min(int(cap), n_days))
    if min_rest > 0:
        rest_cap = max(0, n_days - min_rest) if n_days <= 7 else n_days - math.ceil(min_rest * n_days / 7)
        cap = min(cap, int(rest_cap))
    return cap


def capacity_analysis(problem: SchedulingProblem) -> dict[str, Any]:
    """算出「总需求人班」与「总承载人班」，这是最能说服人的诊断依据。"""
    dates = set(problem.dates)
    shift_ids = {s.id for s in problem.shifts}
    n_days = len(problem.dates)
    rules = problem.rules

    total_demand = sum(
        e.headcount
        for e in problem.demand
        if e.date in dates and e.shift_id in shift_ids and e.headcount > 0
    )

    caps: list[int] = []
    unavailable_hits = 0
    for emp in problem.employees:
        cap = effective_cap(
            emp.max_shifts, rules.max_shifts_per_employee, n_days, rules.min_rest_days_per_week
        )
        blocked = len(set(emp.unavailable_dates) & dates)
        unavailable_hits += blocked
        caps.append(max(0, cap - blocked))

    total_capacity = sum(caps)
    n = max(1, len(problem.employees))
    slots = max(1, n_days * len(problem.shifts))  # 全班次总格数
    return {
        "total_demand": total_demand,
        "total_capacity": total_capacity,
        "gap": total_demand - total_capacity,
        "headcount": len(problem.employees),
        "n_days": n_days,
        "unavailable_hits": unavailable_hits,
        # 每人平均能承担几个班；缺口要补几个人时用得到
        "per_cap": total_capacity // n if n else 0,
        # 每班平均最多能排几个人（「把需求降到多少」的建议值）
        "max_avg_headcount": round(total_capacity / slots, 1),
        "needed_per_person": math.ceil(total_demand / n) if n else 0,
    }


def repair_problem(problem: SchedulingProblem) -> tuple[SchedulingProblem, list[str]]:
    """在建模之前做一次**确定性**的自动修补，兜住 LLM 抽取出来的自相矛盾。

    典型场景：LLM 给某个班次加了「需要收银技能」，却没有给任何员工打上这个技能标签，
    于是一上来就无解。与其让用户对着一句「无解」发呆，不如在这里补齐并**大声说明**。

    返回修补后的新问题对象和修补说明列表（不修改入参，便于对比与回滚）。
    """
    notes: list[str] = []
    fixed = problem.model_copy(deep=True)

    global_cap = fixed.rules.max_shifts_per_employee
    n_days = len(fixed.dates)

    for shift in fixed.shifts:
        if not shift.required_skill:
            continue
        holders = [e for e in fixed.employees if shift.required_skill in e.skills]
        if holders:
            continue
        for emp in fixed.employees:
            if shift.required_skill not in emp.skills:
                emp.skills.append(shift.required_skill)
        notes.append(
            f"班次「{shift.name}」要求技能「{shift.required_skill}」，"
            f"但没有任何员工被标注具备它 —— 已临时默认全员具备以避免直接无解，"
            f"请确认到底谁真的会收银。"
        )

    # 员工被标记为整天不可用，但实际上不可能有任何排班时，给出提示
    for emp in fixed.employees:
        cap = emp.max_shifts if emp.max_shifts is not None else global_cap
        if n_days and cap <= 0:
            notes.append(f"「{emp.name}」的上班天数上限是 0，将不会参与排班。")
        elif n_days and len(set(emp.unavailable_dates) & set(fixed.dates)) >= n_days:
            notes.append(f"「{emp.name}」在整个计划期内都不可用，将不会参与排班。")

    return fixed, notes


def validate_problem(problem: SchedulingProblem) -> list[str]:
    """建模之前做一次语义体检，把明显的脏数据挡在求解器之外。"""
    warnings: list[str] = []

    if not problem.employees:
        warnings.append("没有员工，无法排班")
    if not problem.shifts:
        warnings.append("没有定义任何班次")
    if not problem.dates:
        warnings.append("计划期为空，没有日期")

    date_set = set(problem.dates)
    shift_ids = {s.id for s in problem.shifts}
    emp_ids = [e.id for e in problem.employees]

    dup = {i for i in emp_ids if emp_ids.count(i) > 1}
    if dup:
        warnings.append(f"员工 id 重复：{sorted(dup)}")

    bad_dates = {e.date for e in problem.demand if e.date not in date_set}
    if bad_dates:
        warnings.append(f"需求里出现计划期之外的日期 {sorted(bad_dates)[:3]}，已忽略")

    bad_shifts = {e.shift_id for e in problem.demand if e.shift_id not in shift_ids}
    if bad_shifts:
        warnings.append(f"需求里出现未定义的班次 {sorted(bad_shifts)[:3]}，已忽略")

    # 技能供给检查 —— 这是最常见的「隐性无解」来源
    for s in problem.shifts:
        if not s.required_skill:
            continue
        holders = sum(1 for e in problem.employees if s.required_skill in e.skills)
        peak = max((d.headcount for d in problem.demand if d.shift_id == s.id), default=0)
        if holders < peak:
            warnings.append(
                f"班次「{s.name}」要求技能「{s.required_skill}」，具备者仅 {holders} 人，"
                f"但单日最高需求 {peak} 人 —— 大概率无解"
            )

    # 总量供给检查
    ca = capacity_analysis(problem)
    if ca["gap"] > 0 and problem.employees:
        warnings.append(
            f"总需求 {ca['total_demand']} 人班 > 总承载 {ca['total_capacity']} 人班，缺口 {ca['gap']} 人班"
        )

    return warnings


# --------------------------------------------------------------------------- #
# 建模
# --------------------------------------------------------------------------- #


class _Built:
    """一次建模的产物集合。"""

    def __init__(self) -> None:
        self.model = cp_model.CpModel()
        self.x: dict[tuple[str, str, str], Any] = {}
        self.work: dict[tuple[str, str], Any] = {}
        self.assumptions: list[Any] = []
        self.assumption_meta: list[tuple[str, str]] = []
        self.rule_instances: dict[str, list[str]] = {}
        self.penalty_terms: list[Any] = []
        self.reporters: list[tuple[str, str, Any]] = []
        self.objective_terms: list[Any] = []


def _build(problem: SchedulingProblem, *, soft: bool) -> _Built:
    """构建 CP-SAT 模型。

    soft=False → 每类规则一个假设字面量，用于可行性判定与**类别级**冲突诊断。
    soft=True  → 规则转成带罚分的软约束，用于求「违约最小的方案」。
    """
    b = _Built()
    model = b.model
    dates = list(problem.dates)
    shifts = list(problem.shifts)
    rules = problem.rules
    n_days = len(dates)
    date_set = set(dates)
    shift_by_id = {s.id: s for s in shifts}

    # 类别级假设字面量（硬模式）
    cat_lit: dict[str, Any] = {}

    def gate(rule: str) -> Any | None:
        """取得该规则类别的假设字面量（懒创建）。soft 模式下返回 None。"""
        if soft:
            return None
        if rule not in cat_lit:
            cat_lit[rule] = model.NewBoolVar(f"rule_{rule}")
        return cat_lit[rule]

    def instance(rule: str, detail: str) -> None:
        b.rule_instances.setdefault(rule, []).append(detail)

    # ---------- 决策变量：员工×日期×班次 ----------
    for e in problem.employees:
        for d in dates:
            for s in shifts:
                b.x[(e.id, d, s.id)] = model.NewBoolVar(f"x_{e.id}_{d}_{s.id}")

    # ---------- 每人每天最多一个班（结构性硬约束，永不放宽） ----------
    for e in problem.employees:
        for d in dates:
            w = model.NewBoolVar(f"w_{e.id}_{d}")
            model.Add(sum(b.x[(e.id, d, s.id)] for s in shifts) == w)
            b.work[(e.id, d)] = w

    # ---------- 技能匹配（物理上不可能，永不放宽） ----------
    for e in problem.employees:
        for s in shifts:
            if s.required_skill and s.required_skill not in e.skills:
                for d in dates:
                    model.Add(b.x[(e.id, d, s.id)] == 0)

    # ---------- 规则①：各班次人数需求 ----------
    demand_map: dict[tuple[str, str], int] = {}
    for entry in problem.demand:
        if entry.date in date_set and entry.shift_id in shift_by_id and entry.headcount > 0:
            key = (entry.date, entry.shift_id)
            demand_map[key] = demand_map.get(key, 0) + entry.headcount

    for (d, sid), need in sorted(demand_map.items()):
        detail = f"{d} 的「{shift_by_id[sid].name}」需要 {need} 人"
        instance("demand", detail)
        covered = sum(b.x[(e.id, d, sid)] for e in problem.employees)
        if soft:
            under = model.NewIntVar(0, need, f"under_demand_{d}_{sid}")
            model.Add(covered + under >= need)
            b.penalty_terms.append(under * PENALTY["demand"])
            b.reporters.append(("demand", detail, under))
        else:
            model.Add(covered >= need).OnlyEnforceIf(gate("demand"))

    # ---------- 规则②：不可用日期 ----------
    for e in problem.employees:
        for d in sorted(set(e.unavailable_dates) & date_set):
            detail = f"{e.name} 在 {d} 不可上班"
            instance("availability", detail)
            if soft:
                hit = model.NewBoolVar(f"avail_viol_{e.id}_{d}")
                model.Add(b.work[(e.id, d)] <= hit)
                b.penalty_terms.append(hit * PENALTY["availability"])
                b.reporters.append(("availability", detail, hit))
            else:
                model.Add(b.work[(e.id, d)] == 0).OnlyEnforceIf(gate("availability"))

    # ---------- 规则③：个人上班天数上限 ----------
    for e in problem.employees:
        cap = e.max_shifts if e.max_shifts is not None else rules.max_shifts_per_employee
        cap = max(0, min(int(cap), n_days))
        load = sum(b.work[(e.id, d)] for d in dates)
        detail = f"{e.name} 最多上 {cap} 天班"
        instance("max_shifts", detail)
        if cap >= n_days:
            continue  # 这条规则不可能被违反，不建约束也不参与诊断
        if soft:
            over = model.NewIntVar(0, n_days - cap, f"over_shifts_{e.id}")
            model.Add(load <= cap + over)
            b.penalty_terms.append(over * PENALTY["max_shifts"])
            b.reporters.append(("max_shifts", detail, over))
        else:
            model.Add(load <= cap).OnlyEnforceIf(gate("max_shifts"))

    # ---------- 规则④：每 7 天最少休息天数 ----------
    min_rest = max(0, min(rules.min_rest_days_per_week, 7))
    if min_rest > 0:
        for window in _sliding_windows(dates, min(7, n_days)):
            max_work = len(window) - min_rest
            if max_work >= len(window):
                continue
            for e in problem.employees:
                load = sum(b.work[(e.id, d)] for d in window)
                detail = f"{e.name} 在 {window[0]}~{window[-1]} 需至少休 {min_rest} 天"
                instance("rest_days", detail)
                if soft:
                    short = model.NewIntVar(0, min_rest, f"rest_short_{e.id}_{window[0]}")
                    model.Add(load <= max_work + short)
                    b.penalty_terms.append(short * PENALTY["rest_days"])
                    b.reporters.append(("rest_days", detail, short))
                else:
                    model.Add(load <= max_work).OnlyEnforceIf(gate("rest_days"))

    # ---------- 规则⑤：连续工作天数上限 ----------
    k = max(1, int(rules.max_consecutive_work_days))
    if k < n_days:
        for window in _sliding_windows(dates, k + 1):
            for e in problem.employees:
                load = sum(b.work[(e.id, d)] for d in window)
                detail = f"{e.name} 在 {window[0]}~{window[-1]} 会连续工作 {k + 1} 天"
                instance("max_consecutive", detail)
                if soft:
                    over = model.NewIntVar(0, 1, f"consec_{e.id}_{window[0]}")
                    model.Add(load <= k + over)
                    b.penalty_terms.append(over * PENALTY["max_consecutive"])
                    b.reporters.append(("max_consecutive", detail, over))
                else:
                    model.Add(load <= k).OnlyEnforceIf(gate("max_consecutive"))

    # ---------- 目标 1：工作量公平（最大-最小上班天数差） ----------
    if len(problem.employees) >= 2 and n_days > 0:
        loads = [sum(b.work[(e.id, d)] for d in dates) for e in problem.employees]
        mx = model.NewIntVar(0, n_days, "max_load")
        mn = model.NewIntVar(0, n_days, "min_load")
        model.AddMaxEquality(mx, loads)
        model.AddMinEquality(mn, loads)
        b.objective_terms.append((mx - mn) * 10)

    # ---------- 目标 2：尽量满足个人休息偏好（软） ----------
    for e in problem.employees:
        for d in sorted(set(e.preferred_off_dates) & date_set):
            b.objective_terms.append(b.work[(e.id, d)] * 3)

    # ---------- 汇总目标 ----------
    model.Minimize(sum(b.penalty_terms) + sum(b.objective_terms))

    if not soft:
        b.assumptions = list(cat_lit.values())
        b.assumption_meta = [
            (rule, _summarize_instances(b.rule_instances.get(rule, [])))
            for rule in cat_lit
        ]
    return b


def _summarize_instances(details: list[str]) -> str:
    """把同一规则下的多条实例压缩成一句话，避免诊断刷屏。"""
    if not details:
        return ""
    if len(details) == 1:
        return details[0]
    return f"{details[0]} 等 {len(details)} 处"


# --------------------------------------------------------------------------- #
# 求解
# --------------------------------------------------------------------------- #


def _new_solver(time_limit: float, workers: int) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = max(1, workers)
    solver.parameters.random_seed = 42
    return solver


def _extract(problem: SchedulingProblem, solver: cp_model.CpSolver, b: _Built) -> list[Assignment]:
    shift_name = {s.id: s.name for s in problem.shifts}
    name_of = {e.id: e.name for e in problem.employees}
    out: list[Assignment] = []
    for e in problem.employees:
        for d in problem.dates:
            for s in problem.shifts:
                if solver.Value(b.x[(e.id, d, s.id)]) == 1:
                    out.append(
                        Assignment(
                            date=d,
                            shift_id=s.id,
                            shift_name=shift_name[s.id],
                            employee_id=e.id,
                            employee_name=name_of[e.id],
                        )
                    )
    out.sort(key=lambda a: (a.date, a.shift_id, a.employee_id))
    return out


def _satisfiable(problem: SchedulingProblem, rule_names: set[str], time_limit: float) -> bool:
    """只保留指定规则类别，判断这些规则放在一起是否可满足。"""
    b = _build(problem, soft=False)
    lits = [
        lit
        for (rule, _detail), lit in zip(b.assumption_meta, b.assumptions)
        if rule in rule_names
    ]
    if not lits:
        return True  # 一条规则都不要求 → 空排班永远可行
    b.model.AddAssumptions(lits)
    s = _new_solver(time_limit, 1)
    return s.Solve(b.model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def _minimal_conflict_rules(
    problem: SchedulingProblem, candidates: list[str], time_limit: float = 5.0
) -> list[str]:
    """删除法（delta-debugging）求**最小**冲突规则集。

    `SufficientAssumptionsForInfeasibility()` 给的是「充分集」，往往包含全部规则，
    对业务方没有信息量。这里逐条尝试删除：删掉之后仍然无解，说明这条不是必需的，
    于是真的删掉。剩下的就是真正互相打架的那几条。
    """
    core = list(candidates)
    for rule in list(candidates):
        if len(core) <= 1:
            break
        trial = [r for r in core if r != rule]
        if trial and not _satisfiable(problem, set(trial), time_limit):
            core = trial
    return core


def _diagnose_conflicts(b: _Built, core: list[Any]) -> list[Conflict]:
    """把求解器返回的字面量翻译成「规则类别」级别的冲突。

    OR-Tools 的 `SufficientAssumptionsForInfeasibility()` 返回的是**字面量在模型中的索引**
    （即 BoolVar.Index()），不是假设列表的下标 —— 这里必须先建反查表。
    """
    lit_index_to_rule: dict[int, str] = {}
    for (rule, _detail), lit in zip(b.assumption_meta, b.assumptions):
        try:
            lit_index_to_rule[lit.Index()] = rule
        except AttributeError:
            continue

    rules: list[str] = []
    for item in core:
        rule = lit_index_to_rule.get(int(item))
        if rule is None and 0 <= int(item) < len(b.assumption_meta):
            rule = b.assumption_meta[int(item)][0]  # 兜底：万一某个版本返回下标
        if rule and rule not in rules:
            rules.append(rule)

    conflicts: list[Conflict] = []
    for rule in rules:
        conflicts.append(
            Conflict(
                rule=rule,
                label=RULE_LABELS.get(rule, rule),
                detail=_summarize_instances(b.rule_instances.get(rule, [])),
            )
        )
    return conflicts


def _rules_of(b: _Built, core: list[Any]) -> list[str]:
    """只做字面量 → 规则名的映射，不做 Conflict 构造。"""
    return [c.rule for c in _diagnose_conflicts(b, core)]


def _conflicts_from_rules(b: _Built, rules: list[str]) -> list[Conflict]:
    return [
        Conflict(
            rule=rule,
            label=RULE_LABELS.get(rule, rule),
            detail=_summarize_instances(b.rule_instances.get(rule, [])),
        )
        for rule in rules
    ]



def _explain(problem: SchedulingProblem, conflicts: list[Conflict]) -> str:
    """生成面向业务人员的中文诊断。"""
    lines: list[str] = []

    ca = capacity_analysis(problem)
    gap = ca["gap"]

    if conflicts:
        lines.append(
            "这组约束**无法同时满足**。求解器定位到一组**最小冲突规则**"
            "（去掉其中任意一条，剩下的规则都能满足）："
        )
        for c in conflicts:
            suffix = f"（{c.detail}）" if c.detail else ""
            lines.append(f"- **{c.label}**{suffix}")
    else:
        lines.append("这组约束**无法同时满足**，但未能定位到单一规则，通常是多条规则叠加所致。")

    # 最有说服力的一段：总量算术
    if gap > 0:
        lines.append("")
        lines.append(
            f"**根因（总量算术）**：全部需求合计 **{ca['total_demand']} 人班**，"
            f"而 {ca['headcount']} 名员工在现有规则下最多只能承担 "
            f"**{ca['total_capacity']} 人班**，缺口 **{gap} 人班**。"
            f"只要需求大于承载，无论怎么排都排不出来。"
        )
        suggestions: list[str] = []
        if ca["needed_per_person"] <= ca["n_days"]:
            suggestions.append(f"把每人上班天数上限提到 **{ca['needed_per_person']} 天**")
        suggestions.append(f"把每班平均需求降到 **{ca['max_avg_headcount']} 人**")
        extra = math.ceil(gap / ca["per_cap"]) if ca["per_cap"] else gap
        if extra > 0:
            suggestions.append(f"再增加约 **{extra} 名**具备相应技能的员工")
        lines.append("**可选调整（任选其一）：** " + "；".join(suggestions) + "。")
    elif conflicts:
        lines.append("")
        lines.append("**建议的调整方向：**")
        for c in conflicts:
            lines.append(f"- {c.label} → {RULE_ADVICE.get(c.rule, '放宽该约束。')}")

    if ca["unavailable_hits"]:
        lines.append("")
        lines.append(
            f"另外，不可用日期共占用 {ca['unavailable_hits']} 个人班，已从承载能力中扣除。"
        )

    return "\n".join(lines)


def solve(
    problem: SchedulingProblem,
    *,
    time_limit: float = 15.0,
    workers: int = 8,
    auto_relax: bool = True,
) -> SolveResult:
    """求解排班问题；无解时自动诊断冲突并给出松弛方案。"""
    if not problem.employees or not problem.shifts or not problem.dates:
        return SolveResult(
            status="MODEL_INVALID",
            diagnosis="模型不完整：" + "；".join(validate_problem(problem)),
        )

    t0 = time.perf_counter()

    # ---------- 第一段：硬约束求解 ----------
    built = _build(problem, soft=False)
    solver = _new_solver(time_limit, workers)
    if built.assumptions:
        built.model.AddAssumptions(built.assumptions)
    status = solver.Solve(built.model)
    elapsed = time.perf_counter() - t0

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(
            status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            assignments=_extract(problem, solver, built),
            objective=solver.ObjectiveValue() if solver.ObjectiveValue() is not None else None,
            solve_time_seconds=elapsed,
            stats={
                "solve_time": round(elapsed, 3),
                "conflicts": 0,
                "status": solver.StatusName(status),
                "rules_checked": len(built.assumptions),
            },
        )

    if status == cp_model.UNKNOWN:
        return SolveResult(
            status="UNKNOWN",
            solve_time_seconds=elapsed,
            diagnosis=f"在 {time_limit:.0f} 秒内没有找到可行解，可以调大时间上限或简化约束。",
        )

    # ---------- 第二段：类别级冲突诊断 ----------
    try:
        core = list(solver.SufficientAssumptionsForInfeasibility())
    except Exception:  # noqa: BLE001 - 不同 OR-Tools 版本行为略有差异
        core = []

    conflicts = _diagnose_conflicts(built, core)

    # 把「充分集」收敛成「最小冲突集」，让结论真正可读
    candidates = _rules_of(built, core) or [rule for rule, _ in built.assumption_meta]
    if len(candidates) > 1:
        try:
            candidates = _minimal_conflict_rules(problem, candidates)
        except Exception:  # noqa: BLE001 - 诊断失败不应影响主流程
            pass
        conflicts = _conflicts_from_rules(built, candidates)

    diagnosis = _explain(problem, conflicts)

    if not auto_relax:
        return SolveResult(
            status="INFEASIBLE",
            solve_time_seconds=elapsed,
            conflicts=conflicts,
            diagnosis=diagnosis,
            stats={"solve_time": round(elapsed, 3), "conflicts": len(conflicts)},
        )

    # ---------- 第三段：自动松弛，求违约最小的方案 ----------
    t1 = time.perf_counter()
    relaxed = _build(problem, soft=True)
    rsolver = _new_solver(time_limit, workers)
    rstatus = rsolver.Solve(relaxed.model)
    r_elapsed = time.perf_counter() - t1

    if rstatus not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(
            status="INFEASIBLE",
            solve_time_seconds=elapsed + r_elapsed,
            conflicts=conflicts,
            diagnosis=diagnosis + "\n\n松弛后仍未找到可行方案，请显著放宽约束。",
            stats={"solve_time": round(elapsed + r_elapsed, 3)},
        )

    # 汇总违反情况：按规则类别聚合，避免刷屏
    per_rule: dict[str, list[str]] = {}
    for rule, detail, var in relaxed.reporters:
        val = int(rsolver.Value(var))
        if val > 0:
            per_rule.setdefault(rule, []).append(detail)
    violations = [
        f"{RULE_LABELS.get(rule, rule)}：{_summarize_instances(items)}（共 {len(items)} 处）"
        for rule, items in sorted(per_rule.items())
    ]

    return SolveResult(
        status="INFEASIBLE",
        assignments=_extract(problem, rsolver, relaxed),
        objective=rsolver.ObjectiveValue() if rsolver.ObjectiveValue() is not None else None,
        solve_time_seconds=elapsed + r_elapsed,
        conflicts=conflicts,
        violations=violations,
        diagnosis=diagnosis,
        stats={
            "solve_time": round(elapsed + r_elapsed, 3),
            "conflicts": len(conflicts),
            "violations": len(violations),
            "note": "原约束下无解；下面给出的是「违约最小」的可行方案，供你确认后再调整。",
        },
    )


# --------------------------------------------------------------------------- #
# 展示辅助（CLI demo 与前端共用一份渲染逻辑）
# --------------------------------------------------------------------------- #


def render_schedule_table(problem: SchedulingProblem, result: SolveResult) -> str:
    """把排班结果渲染成 Markdown 表格：行=员工，列=日期。"""
    if not result.assignments:
        return "（没有可展示的排班）"

    cell: dict[tuple[str, str], list[str]] = {}
    for a in result.assignments:
        cell.setdefault((a.employee_id, a.date), []).append(a.shift_name)

    header = "| 员工 | " + " | ".join(problem.dates) + " | 上班 | 休息 |"
    sep = "| --- | " + " | ".join("---" for _ in problem.dates) + " | --- | --- |"
    lines = [header, sep]
    for e in problem.employees:
        row = [e.name]
        worked = 0
        for d in problem.dates:
            marks = cell.get((e.id, d), [])
            row.append(" / ".join(marks) if marks else "休")
            if marks:
                worked += 1
        row.append(str(worked))
        row.append(str(len(problem.dates) - worked))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
