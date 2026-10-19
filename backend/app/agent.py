"""Agent 编排：一条显式的状态机。

    自然语言 ──S1 理解──▶ 结构化参数 ──S2 校验──▶ 数学模型
                                                     │
        ┌──────────── S3 求解 ◀──────────────────────┘
        │
        ├─ 有解 ──▶ S5 生成决策说明 ──▶ 返回
        │
        └─ 无解 ──▶ S4 冲突诊断 + 自动松弛 ──▶ S5 生成决策说明 ──▶ 返回

为什么用显式状态机而不是 ReAct 循环？
- 每一步的输入输出都用 JSON Schema 卡死，幻觉跑不出去；
- 失败可以回到上一步，过程可复现、可评测；
- 每一步都能对外暴露成 trace，前端可以实时展示「Agent 在想什么」。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys as _sys
import time
from datetime import date as _date
from pathlib import Path as _Path
from typing import Literal

from pydantic import BaseModel, Field

# 允许 `python app/agent.py` 直接运行：把 backend/ 加进模块搜索路径。
# 正常 `python -m app.agent` 时 __package__ 是 "app"，这段不执行。
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from app import prompts
from app.llm import LLMClient, LLMError
from app.schemas import (
    AgentStep,
    ExtractionResult,
    SchedulingProblem,
    SolveResult,
    WhatIfPatch,
    WhatIfResult,
)
from app.solver.scheduling import (
    RULE_LABELS,
    render_schedule_table,
    repair_problem,
    solve,
    validate_problem,
)
from app.whatif import apply_patch, compute_diff


# --------------------------------------------------------------------------- #
# 数字护栏：LLM 最爱在「总量」上瞎算，这里做一次事实核查
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in _NUM_RE.findall(text or ""):
        cleaned = m.lstrip("0") or "0"
        out.add(cleaned)
    return out


def number_guard(narrative: str, allowed_text: str) -> list[str]:
    """返回决策说明里「来源不明」的数字。

    LLM 把 28 个班次写成 35 个这种事，肉眼很难发现，但在答辩现场很致命。
    这里用确定性规则兜住：文中每个数字都必须能在事实材料里找到。
    """
    allowed = _numbers_in(allowed_text) | {str(i) for i in range(1, 11)}
    used = _numbers_in(narrative)
    return sorted(used - allowed, key=lambda x: (len(x), x))



# --------------------------------------------------------------------------- #
# 运行轨迹
# --------------------------------------------------------------------------- #


class AgentRun(BaseModel):
    ok: bool = False
    steps: list[AgentStep] = Field(default_factory=list)
    problem: SchedulingProblem | None = None
    extraction: ExtractionResult | None = None
    result: SolveResult | None = None
    narrative: str = ""
    warnings: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list, description="建模前的自动修补记录")
    error: str = ""

    # 前端直接可用的派生字段
    schedule_table_md: str = ""
    truncated: bool = False


STEP_NAMES: list[tuple[str, str]] = [
    ("understand", "理解需求"),
    ("validate", "数据校验"),
    ("model", "数学建模"),
    ("solve", "精确求解"),
    ("diagnose", "冲突诊断"),
    ("explain", "生成决策说明"),
]


def _new_steps() -> list[AgentStep]:
    return [AgentStep(key=k, name=n) for k, n in STEP_NAMES]


def _find(steps: list[AgentStep], key: str) -> AgentStep:
    for s in steps:
        if s.key == key:
            return s
    raise KeyError(key)


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #


class DecisionAgent:
    """大模型驱动的智能运筹决策 Agent。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    # -- S1：自然语言 → 结构化参数 ---------------------------------------- #

    async def understand(self, text: str) -> ExtractionResult:
        system = prompts.EXTRACTION_SYSTEM.format(today=_date.today().isoformat())
        user = prompts.EXTRACTION_USER.format(text=text.strip())
        extraction, _raw = await self.llm.chat_json(system, user, ExtractionResult)

        # LLM 有可能漏掉 source_text，这里兜底补上，便于追溯
        if not extraction.problem.source_text:
            extraction.problem.source_text = text.strip()

        # 日期排序与去重，保证后续建模的「滑动窗口」有意义
        extraction.problem.dates = sorted(set(extraction.problem.dates))
        return extraction

    # -- 确定性事实数字（既喂给 LLM，也作为数字护栏的白名单） --------------- #

    @staticmethod
    def fact_numbers(problem: SchedulingProblem, result: SolveResult) -> dict[str, float | int]:
        """全部「可被引用」的关键数字，一律由后端算好，不让 LLM 算。"""
        numbers: dict[str, float | int] = {
            "员工数": len(problem.employees),
            "计划天数": len(problem.dates),
            "班次类型数": len(problem.shifts),
            "日报需求总数": sum(d.headcount for d in problem.demand),
            "总排班数": len(result.assignments),
            "求解耗时秒": round(result.solve_time_seconds, 3),
        }
        per_emp: dict[str, int] = {}
        for a in result.assignments:
            per_emp[a.employee_name] = per_emp.get(a.employee_name, 0) + 1
        if per_emp:
            numbers["人均上班天数"] = round(sum(per_emp.values()) / len(per_emp), 2)
            numbers["最多上班天数"] = max(per_emp.values())
            numbers["最少上班天数"] = min(per_emp.values())
            numbers["参与排班人数"] = len(per_emp)
        return numbers

    # -- S5：结果 → 自然语言决策说明 --------------------------------------- #

    async def explain(self, problem: SchedulingProblem, result: SolveResult) -> str:
        numbers = self.fact_numbers(problem, result)

        conflicts = "；".join(f"{c.label}（{c.detail}）" for c in result.conflicts) or "无"
        violations = "；".join(result.violations) or "无"

        # 关键：把求解器算好的「确定性诊断」（含精确的调整建议）也传给模型，
        # 并要求它直接引用，否则模型会自己另算一套「加 1 个人 / 降到 2 人」之类的错误建议，
        # 与冲突诊断卡片上的正确建议互相矛盾。
        diagnosis = result.diagnosis or "无（约束可满足）"

        return await self.llm.chat_text(
            prompts.NARRATIVE_SYSTEM,
            prompts.NARRATIVE_USER.format(
                background=problem.source_text or problem.title,
                status=result.status,
                numbers=json.dumps(numbers, ensure_ascii=False, indent=2),
                conflicts=conflicts,
                diagnosis=diagnosis,
                violations=violations,
            ),
        )

    # -- 主流程 ------------------------------------------------------------ #

    async def run(self, text: str, *, skip_narrative: bool = False) -> AgentRun:
        run = AgentRun(steps=_new_steps())

        if not text or not text.strip():
            run.error = "输入为空，请描述你的排班需求。"
            _find(run.steps, "understand").status = "error"
            return run

        if not self.llm.enabled:
            run.error = (
                "未配置大模型 API Key。请在 backend/.env 中设置 DEEPSEEK_API_KEY；"
                "若只想联调前端，可设置 LLM_MOCK=1 使用离线样例。"
            )
            for s in run.steps:
                s.status = "error" if s.key == "understand" else "pending"
            return run

        # ---------- S1 理解需求 ----------
        step = _find(run.steps, "understand")
        step.status = "running"
        t0 = time.perf_counter()
        try:
            extraction = await self.understand(text)
        except LLMError as exc:
            step.status = "error"
            step.detail = str(exc)
            step.duration_ms = int((time.perf_counter() - t0) * 1000)
            run.error = str(exc)
            return run
        step.status = "ok"
        step.duration_ms = int((time.perf_counter() - t0) * 1000)
        step.detail = extraction.summary or "已抽取结构化参数"
        run.extraction = extraction
        problem = extraction.problem

        # ---------- S2 数据校验 + 确定性自动修补 ----------
        step = _find(run.steps, "validate")
        step.status = "running"
        warnings = validate_problem(problem)

        # 修补 LLM 抽出来的自相矛盾（例如班次要求某技能，却没人具备）
        problem, repairs = repair_problem(problem)
        run.problem = problem
        run.repairs = repairs
        warnings = warnings + repairs
        run.warnings = warnings
        step.duration_ms = 0
        if warnings:
            step.status = "warn"
            step.detail = "；".join(warnings)
        else:
            step.status = "ok"
            step.detail = (
                f"{len(problem.employees)} 名员工 / {len(problem.shifts)} 个班次 / "
                f"{len(problem.dates)} 天 / {len(problem.demand)} 条需求"
            )

        # ---------- S3 数学建模 + S4 求解 ----------
        step = _find(run.steps, "model")
        step.status = "ok"
        step.detail = (
            f"决策变量约 {len(problem.employees) * len(problem.dates) * len(problem.shifts)} 个；"
            f"规则：{'、'.join(sorted({RULE_LABELS.get(k, k) for k in RULE_LABELS}))}"
        )

        step = _find(run.steps, "solve")
        step.status = "running"
        t1 = time.perf_counter()
        try:
            result = await asyncio.to_thread(solve, problem, time_limit=15.0)
        except Exception as exc:  # noqa: BLE001 - 建模异常要兜住并给出可读信息
            step.status = "error"
            step.detail = f"求解器异常：{exc}"
            step.duration_ms = int((time.perf_counter() - t1) * 1000)
            run.error = f"求解器异常：{exc}"
            return run
        step.duration_ms = int((time.perf_counter() - t1) * 1000)
        run.result = result

        # ---------- S5 冲突诊断 ----------
        diag = _find(run.steps, "diagnose")
        if result.conflicts or result.status == "INFEASIBLE":
            diag.status = "warn"
            diag.detail = (
                f"定位到 {len(result.conflicts)} 处冲突"
                + (f"，已自动松弛出违约最小的方案（{len(result.violations)} 处违反）"
                   if result.assignments else "")
            )
            step.status = "warn"
            step.detail = f"{result.status} —— 约束冲突，已进入自愈流程"
        else:
            diag.status = "ok"
            diag.detail = "约束可满足，无需松弛"
            step.status = "ok" if result.status in ("OPTIMAL", "FEASIBLE") else "warn"
            step.detail = f"{result.status}（{result.solve_time_seconds:.2f}s）"

        # ---------- S6 生成决策说明 ----------
        if result.assignments:
            run.schedule_table_md = render_schedule_table(problem, result)

        exp = _find(run.steps, "explain")
        if skip_narrative:
            exp.status = "ok"
            exp.detail = "已跳过"
        else:
            exp.status = "running"
            t2 = time.perf_counter()
            try:
                run.narrative = await self.explain(problem, result)
                exp.status = "ok"
                exp.detail = "已生成中文决策说明"

                # 事实核查：说明里的数字必须能在「事实材料」里找到
                allowed_text = (
                    problem.model_dump_json()
                    + run.schedule_table_md
                    + result.model_dump_json()
                    + json.dumps(self.fact_numbers(problem, result), ensure_ascii=False)
                )
                suspicious = number_guard(run.narrative, allowed_text)
                if suspicious:
                    exp.status = "warn"
                    exp.detail = (
                        "已生成决策说明，但发现来源不明的数字："
                        + "、".join(suspicious[:5])
                        + "（可能是模型自行计算，请以表格为准）"
                    )
                    run.warnings.append(exp.detail)
            except LLMError as exc:
                exp.status = "warn"
                exp.detail = f"决策说明生成失败（不影响方案）：{exc}"
            exp.duration_ms = int((time.perf_counter() - t2) * 1000)

        run.ok = bool(result.assignments) or result.status in ("OPTIMAL", "FEASIBLE")
        return run

    # ------------------------------------------------------------------ #
    # What-if：如果……会怎样
    # ------------------------------------------------------------------ #

    @staticmethod
    def _context_block(problem: SchedulingProblem) -> str:
        """把当前问题压缩成一段紧凑上下文，喂给「变更意图解析器」。"""
        lines = [f"计划期：{problem.dates[0]} ~ {problem.dates[-1]}（共 {len(problem.dates)} 天）"]
        lines.append(
            "员工：" + "、".join(
                f"{e.id}={e.name}" + (f"[{'/'.join(e.skills)}]" if e.skills else "")
                + (f"(不可用:{','.join(e.unavailable_dates)})" if e.unavailable_dates else "")
                for e in problem.employees
            )
        )
        lines.append(
            "班次：" + "、".join(
                f"{s.id}={s.name}"
                + (f"(需{s.required_skill})" if s.required_skill else "")
                for s in problem.shifts
            )
        )
        lines.append(
            "规则：每人最多 "
            f"{problem.rules.max_shifts_per_employee} 天，每周至少休 "
            f"{problem.rules.min_rest_days_per_week} 天，连续工作不超过 "
            f"{problem.rules.max_consecutive_work_days} 天"
        )
        by_shift: dict[str, int] = {}
        for d in problem.demand:
            by_shift[d.shift_id] = max(by_shift.get(d.shift_id, 0), d.headcount)
        lines.append("人数需求（每日峰值）：" + "、".join(f"{k}={v}人" for k, v in by_shift.items()))
        return "\n".join(lines)

    async def whatif(
        self, problem: SchedulingProblem, text: str, *, skip_narrative: bool = False
    ) -> WhatIfResult:
        """对现有方案做「如果……会怎样」：解析变更 → 应用 → 重解 → 对比。"""
        run = WhatIfResult(
            steps=[
                AgentStep(key="parse", name="解析假设"),
                AgentStep(key="patch", name="应用变更"),
                AgentStep(key="solve", name="重新求解"),
                AgentStep(key="diff", name="方案对比"),
                AgentStep(key="explain", name="说明影响"),
            ]
        )

        if not text.strip():
            run.error = "请描述你想验证的假设，例如「如果张伟周三请假会怎样」。"
            return run

        if not self.llm.enabled:
            run.error = "未配置大模型 API Key，无法解析自然语言假设。"
            return run

        # ---------- 1. 解析假设 ----------
        step = run.steps[0]
        step.status = "running"
        t0 = time.perf_counter()
        system = prompts.WHATIF_SYSTEM.format(context=self._context_block(problem))
        try:
            patch, _raw = await self.llm.chat_json(
                system, prompts.WHATIF_USER.format(text=text.strip()), WhatIfPatch
            )
        except LLMError as exc:
            step.status = "error"
            step.detail = str(exc)
            run.error = str(exc)
            return run
        step.duration_ms = int((time.perf_counter() - t0) * 1000)
        run.understood = patch.understood
        run.ops = patch.ops

        if not patch.ops:
            step.status = "warn"
            step.detail = patch.understood or "没能从这句话里识别出可执行的变更"
            run.error = "没能识别出可执行的变更。试试更具体的说法，例如「如果张伟周三请假会怎样」。"
            return run

        step.status = "ok"
        step.detail = patch.understood or f"识别到 {len(patch.ops)} 处变更"

        # ---------- 2. 应用变更 ----------
        step = run.steps[1]
        step.status = "running"
        patched, notes = apply_patch(problem, patch.ops)
        run.patched_problem = patched
        run.notes = notes
        step.status = "ok"
        step.detail = "；".join(notes) if notes else "没有产生实际变更"

        # ---------- 3. 重新求解（原方案也重算一次，保证对比公平） ----------
        step = run.steps[2]
        step.status = "running"
        t1 = time.perf_counter()
        try:
            baseline = await asyncio.to_thread(solve, problem, time_limit=15.0)
            # 传入 baseline，启用「最小扰动」目标：只改必须改的班次
            after = await asyncio.to_thread(
                solve, patched, time_limit=15.0, baseline=baseline.assignments
            )
        except Exception as exc:  # noqa: BLE001
            step.status = "error"
            step.detail = f"求解异常：{exc}"
            run.error = f"求解异常：{exc}"
            return run
        step.duration_ms = int((time.perf_counter() - t1) * 1000)
        step.status = "ok" if after.assignments else "warn"
        step.detail = (
            f"原方案 {baseline.status}（{len(baseline.assignments)} 条）→ "
            f"新方案 {after.status}（{len(after.assignments)} 条）"
        )
        run.baseline_result = baseline
        run.result = after

        # ---------- 4. 对比 ----------
        step = run.steps[3]
        step.status = "running"
        diff = compute_diff(problem, baseline, patched, after)
        run.diff = diff
        step.status = "ok"
        step.detail = (
            f"取消 {len(diff.removed)} 条、新增 {len(diff.added)} 条，"
            f"未变动 {diff.unchanged_count} 条"
        )

        # ---------- 5. 说明影响 ----------
        step = run.steps[4]
        if skip_narrative:
            step.status = "ok"
            step.detail = "已跳过"
        else:
            step.status = "running"
            t2 = time.perf_counter()
            ops_text = "；".join(
                f"{o.op}({o.employee_name or o.employee_id or ''}{'/' + o.date if o.date else ''}"
                f"{'/' + o.shift_id if o.shift_id else ''}"
                f"{'=' + str(o.headcount) if o.headcount is not None else ''}"
                f"{'=' + str(o.value) if o.value is not None else ''})"
                for o in patch.ops
            )
            diff_facts = {
                "原方案状态": diff.status_before,
                "新方案状态": diff.status_after,
                "原方案排班数": diff.total_before,
                "新方案排班数": diff.total_after,
                "取消的排班数": len(diff.removed),
                "新增的排班数": len(diff.added),
                "未变动的排班数": diff.unchanged_count,
                "受影响最大的员工": [
                    f"{r.name}：{r.before}天 → {r.after}天"
                    for r in diff.per_employee
                    if r.delta != 0
                ][:5]
                or ["无"],
            }
            try:
                run.narrative = await self.llm.chat_text(
                    prompts.WHATIF_NARRATIVE_SYSTEM,
                    prompts.WHATIF_NARRATIVE_USER.format(
                        text=text.strip(),
                        ops=ops_text,
                        diff=json.dumps(diff_facts, ensure_ascii=False, indent=2),
                        diagnosis=after.diagnosis or "无",
                    ),
                )
                step.status = "ok"
                step.detail = "已生成影响说明"
                suspicious = number_guard(
                    run.narrative, json.dumps(diff_facts, ensure_ascii=False) + after.model_dump_json()
                )
                if suspicious:
                    step.status = "warn"
                    step.detail = "已生成影响说明，但发现来源不明的数字：" + "、".join(suspicious[:5])
            except LLMError as exc:
                step.status = "warn"
                step.detail = f"影响说明生成失败（不影响对比结果）：{exc}"
            step.duration_ms = int((time.perf_counter() - t2) * 1000)

        run.ok = bool(after.assignments) or after.status in ("OPTIMAL", "FEASIBLE")
        return run


# --------------------------------------------------------------------------- #
# 命令行入口：想在终端里单独跑一次 Agent 时用
#
#   cd backend
#   ..\.venv\Scripts\python.exe -m app.agent "我们便利店下周一到周日要排班……"
#
# 注意：必须用 `-m app.agent`，不能直接 `python app/agent.py`。
# 直接运行文件时 sys.path 里是 backend/app/，`from app import prompts` 找不到包。
# --------------------------------------------------------------------------- #

_DEMO_TEXT = (
    "我们便利店下周一到周日要排班。一共 7 个店员：张伟、李娜、王强、刘洋、陈静、赵磊、孙悦。"
    "每天分早班和晚班，晚班需要会收银的人。每个班次每天要 2 个人。"
    "每人每周最多上 5 天班，每周至少休息 1 天。张伟下周三请假，李娜周六希望休息。"
)


async def _cli() -> None:
    import sys
    from pathlib import Path

    # 单独跑本文件时，.env 不会被自动加载（平时由 main.py 负责），这里补上
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    text = " ".join(sys.argv[1:]).strip() or _DEMO_TEXT
    if not sys.argv[1:]:
        print("（未提供需求文本，使用内置示例）\n")

    agent = DecisionAgent()
    if not agent.llm.enabled:
        print("❌ 未配置 API Key。请在 backend/.env 里设置 DEEPSEEK_API_KEY，")
        print("   或设置 LLM_MOCK=1 使用离线样例数据。")
        return

    run = await agent.run(text)

    print("=" * 70)
    print("六步执行轨迹")
    print("=" * 70)
    for s in run.steps:
        mark = {"ok": "[ok   ]", "warn": "[warn ]", "error": "[error]", "pending": "[     ]"}.get(
            s.status, "[?]"
        )
        print(f"  {mark} {s.name:<8} {s.duration_ms:>5}ms  {s.detail}")

    print()
    print("=" * 70)
    print(f"结果：ok={run.ok} | status={(run.result.status if run.result else None)}")
    print("=" * 70)
    if run.warnings:
        print("警告：")
        for w in run.warnings:
            print(f"  - {w}")
        print()
    if run.narrative:
        print("决策说明：")
        print(run.narrative)
    if run.schedule_table_md:
        print()
        print(run.schedule_table_md)


if __name__ == "__main__":
    asyncio.run(_cli())
