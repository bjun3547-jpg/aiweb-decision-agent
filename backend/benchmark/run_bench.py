"""基准测试：三档规模下的求解性能与方案质量。

用途：
    给技术文档 §6.2 的表格 9 提供可复现的数据来源，
    同时也是答辩时「求解器到底多快」这个问题的证据。

用法：
    cd backend
    ..\\.venv\\Scripts\\python.exe benchmark\\run_bench.py            # 默认 3 档 × 5 次
    ..\\.venv\\Scripts\\python.exe benchmark\\run_bench.py -n 10      # 每档 10 次
    ..\\.venv\\Scripts\\python.exe benchmark\\run_bench.py --with-llm # 额外测端到端（真实调模型）

产物：
    benchmark/results/bench.csv     每次运行的明细，可直接导入 Excel
    benchmark/results/bench.md      汇总表，可直接粘进技术文档

设计说明：
    三档规模不是简单地「人多」，而是**保持人均负荷相近**地放大：
        5 人 → 每班 1 人（14 个班次，人均 2.8 天）
        7 人 → 每班 2 人（28 个班次，人均 4.0 天）  ← 与演示场景一致
       12 人 → 每班 3 人（42 个班次，人均 3.5 天）
    如果只加人不加需求，大实例反而更容易，对比就没有意义了。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path

# 允许 `python benchmark/run_bench.py` 直接运行：把 backend/ 加进模块搜索路径
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.schemas import (  # noqa: E402
    DemandEntry,
    Employee,
    SchedulingProblem,
    SchedulingRules,
    ShiftType,
)
from app.solver import solve, validate_problem  # noqa: E402

RESULT_DIR = Path(__file__).resolve().parent / "results"

# 三档规模：员工数 / 每班需要人数 / 收银技能人数
SCALES: list[tuple[str, int, int, int]] = [
    ("5 人", 5, 1, 3),
    ("7 人", 7, 2, 4),
    ("12 人", 12, 3, 6),
]

SURNAMES = "张李王刘陈赵孙周吴郑冯何许邓曹彭曾萧田董潘"
GIVEN = ["伟", "娜", "强", "洋", "静", "磊", "悦", "敏", "涛", "丽", "军", "芳", "杰", "婷", "勇", "雪"]

SHIFTS = [
    ShiftType(id="morning", name="早班", required_skill=None, start_hour=8, end_hour=16),
    ShiftType(id="evening", name="晚班", required_skill="收银", start_hour=16, end_hour=24),
]


# --------------------------------------------------------------------------- #
# 实例生成
# --------------------------------------------------------------------------- #


def next_week() -> list[str]:
    """下周一 ~ 下周日（与 agent 的日期锚点保持同一口径）。"""
    today = _date.today()
    monday = today - _timedelta(days=today.weekday()) + _timedelta(days=7)
    return [(monday + _timedelta(days=i)).isoformat() for i in range(7)]


def build_instance(
    n_employees: int,
    headcount: int,
    n_cashiers: int,
    *,
    seed: int = 42,
) -> SchedulingProblem:
    """生成一个可复现的排班实例。

    可复现很关键：同一 seed 每次生成的实例完全一样，
    否则「这次 40ms、上次 60ms」就分不清是实例变了还是机器抖了。
    """
    rng = random.Random(seed)
    dates = next_week()

    used: set[str] = set()
    employees: list[Employee] = []
    for i in range(n_employees):
        while True:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN)
            if name not in used:
                used.add(name)
                break
        skills = ["收银"] if i < n_cashiers else ["理货"]
        if i == 0:
            skills.append("理货")
        employees.append(
            Employee(
                id=f"E{i + 1:02d}",
                name=name,
                skills=skills,
                max_shifts=5,
                # 第 1 个人请假一天，第 2 个人有一天想休息 —— 让约束不是完全空转
                unavailable_dates=[dates[2]] if i == 0 else [],
                preferred_off_dates=[dates[5]] if i == 1 else [],
            )
        )

    demand = [
        DemandEntry(date=d, shift_id=s.id, headcount=headcount) for d in dates for s in SHIFTS
    ]

    return SchedulingProblem(
        title=f"{n_employees} 人基准实例",
        dates=dates,
        employees=employees,
        shifts=SHIFTS,
        demand=demand,
        rules=SchedulingRules(
            min_rest_days_per_week=1,
            max_consecutive_work_days=6,
            max_shifts_per_employee=5,
            allow_understaffing=False,
        ),
        source_text=f"benchmark: {n_employees} employees, {headcount} per shift",
    )


# --------------------------------------------------------------------------- #
# 单档基准
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    scale: str
    run: int
    status: str
    employees: int
    variables: int
    demand_total: int
    assignments: int
    objective: float | None
    solve_ms: float
    warnings: int


def bench_solver(problem: SchedulingProblem, scale: str, runs: int) -> list[Row]:
    """同一实例重复求解，记录每次的耗时。"""
    variables = len(problem.employees) * len(problem.dates) * len(problem.shifts)
    demand_total = sum(d.headcount for d in problem.demand)
    warnings = validate_problem(problem)

    rows: list[Row] = []
    for r in range(runs):
        t0 = time.perf_counter()
        result = solve(problem, time_limit=15.0)
        wall = (time.perf_counter() - t0) * 1000
        rows.append(
            Row(
                scale=scale,
                run=r + 1,
                status=result.status,
                employees=len(problem.employees),
                variables=variables,
                demand_total=demand_total,
                assignments=len(result.assignments),
                objective=result.objective,
                # 用 solve() 自己记的时间，排除 Python 侧开销
                solve_ms=result.solve_time_seconds * 1000 or wall,
                warnings=len(warnings),
            )
        )
    return rows


def bench_end_to_end(problem: SchedulingProblem, runs: int) -> dict[str, float]:
    """端到端耗时（真实调用大模型）。

    单独放在这里是因为它花钱、且慢，默认不跑。
    """
    from app.agent import DecisionAgent

    # 单独跑本脚本时 .env 不会被自动加载（平时由 main.py 负责），这里补上。
    # 不加载的话 api_key 为空，agent.run() 会立刻失败，测出来的耗时是 0.1ms。
    try:
        from dotenv import load_dotenv

        load_dotenv(_BACKEND / ".env")
    except ImportError:
        pass

    text = (
        f"我们便利店下周一到周日要排班。一共 {len(problem.employees)} 个店员："
        + "、".join(e.name for e in problem.employees)
        + "。每天分早班和晚班，晚班需要会收银的人。"
        f"每个班次每天要 {problem.demand[0].headcount} 个人。"
        "每人每周最多上 5 天班，每周至少休息 1 天。"
    )

    async def _run() -> dict[str, float]:
        agent = DecisionAgent()
        if not agent.llm.enabled:
            raise RuntimeError(
                "端到端测试需要 API Key：请在 backend/.env 里配置 DEEPSEEK_API_KEY，"
                "或先 export LLM_MOCK=1（但 mock 模式下耗时没有参考价值）。"
            )
        samples: list[dict[str, float]] = []
        errors: list[str] = []
        for _ in range(runs):
            t0 = time.perf_counter()
            run = await agent.run(text)
            total = (time.perf_counter() - t0) * 1000
            by_key = {s.key: s.duration_ms for s in run.steps}
            if not run.ok:
                errors.append(run.error or "未知错误")
            samples.append(
                {
                    "total": total,
                    "understand": by_key.get("understand", 0),
                    "solve": by_key.get("solve", 0),
                    "explain": by_key.get("explain", 0),
                    "ok": 1.0 if run.ok else 0.0,
                }
            )
        if errors:
            # 别把失败的一次当成"0ms"混进中位数里
            raise RuntimeError(f"{len(errors)}/{runs} 次运行失败：{errors[0][:200]}")
        return {k: statistics.median(v[k] for v in samples) for k in samples[0]}

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def pct(values: list[float], p: float) -> float:
    """百分位（线性插值），避免为了一个分位数引入 numpy。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def write_csv(rows: list[Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：带 BOM，Excel 双击打开中文不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["规模", "第几次", "状态", "员工数", "决策变量", "需求班次", "实际排班",
             "目标值", "求解耗时(ms)", "校验告警"]
        )
        for r in rows:
            w.writerow(
                [r.scale, r.run, r.status, r.employees, r.variables, r.demand_total,
                 r.assignments,
                 "" if r.objective is None else round(r.objective, 2),
                 round(r.solve_ms, 2), r.warnings]
            )


def summary_md(rows: list[Row], runs: int) -> str:
    lines: list[str] = []
    lines.append(f"### 求解器性能（每档 {runs} 次，同一实例重复求解）\n")
    lines.append("| 规模 | 决策变量 | 需求班次 | 实际排班 | 状态 | 中位数 | P90 | 最大 | 目标值 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    for scale, *_ in SCALES:
        sub = [r for r in rows if r.scale == scale]
        if not sub:
            continue
        times = [r.solve_ms for r in sub]
        status = sub[0].status
        obj = sub[0].objective
        lines.append(
            f"| {scale} | {sub[0].variables} | {sub[0].demand_total} | {sub[0].assignments} "
            f"| {status} | **{statistics.median(times):.0f} ms** | {pct(times, 0.9):.0f} ms "
            f"| {max(times):.0f} ms | {'' if obj is None else f'{obj:.0f}'} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="排班求解器基准测试")
    ap.add_argument("-n", "--runs", type=int, default=5, help="每档重复次数（默认 5）")
    ap.add_argument("--with-llm", action="store_true", help="额外测端到端（真实调模型，慢且花钱）")
    ap.add_argument("--llm-runs", type=int, default=3, help="端到端重复次数（默认 3）")
    ap.add_argument("--seed", type=int, default=42, help="实例随机种子")
    args = ap.parse_args()

    print("=" * 70)
    print("排班求解器基准测试")
    print("=" * 70)
    print(f"重复次数 : 每档 {args.runs} 次")
    print(f"随机种子 : {args.seed}")
    print(f"计划期   : {next_week()[0]} ~ {next_week()[-1]}")
    print()

    all_rows: list[Row] = []
    instances: dict[str, SchedulingProblem] = {}

    for scale, n_emp, headcount, n_cashiers in SCALES:
        problem = build_instance(n_emp, headcount, n_cashiers, seed=args.seed)
        instances[scale] = problem
        print(f"[{scale}] 每班 {headcount} 人，收银 {n_cashiers} 人 …", end="", flush=True)
        rows = bench_solver(problem, scale, args.runs)
        all_rows.extend(rows)
        times = [r.solve_ms for r in rows]
        print(
            f" {rows[0].status} / 中位 {statistics.median(times):.0f} ms "
            f"/ {rows[0].assignments} 条排班 / 目标 {rows[0].objective}"
        )

    print()
    md = summary_md(all_rows, args.runs)
    print(md)
    print()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULT_DIR / "bench.csv"
    write_csv(all_rows, csv_path)
    print(f"明细已写入 {csv_path.relative_to(_BACKEND)}")

    # 端到端（可选）
    if args.with_llm:
        print()
        print(f"端到端测试（真实调用大模型，{args.llm_runs} 次，会产生费用）…")
        try:
            e2e = bench_end_to_end(instances["7 人"], args.llm_runs)
        except Exception as exc:  # noqa: BLE001 - 端到端失败不该让求解器结果白跑
            print(f"  跳过：{type(exc).__name__}: {exc}")
            e2e = None
        if e2e:
            print(
                f"  总耗时 中位 {e2e['total']:.0f} ms "
                f"= 理解需求 {e2e['understand']:.0f} + 求解 {e2e['solve']:.0f} + 生成说明 {e2e['explain']:.0f}"
            )
            ratio = e2e["solve"] / e2e["total"] * 100 if e2e["total"] else 0
            print(f"  求解器占比：{ratio:.2f}%")
            md += (
                f"\n\n### 端到端耗时（7 人档，{args.llm_runs} 次中位数）\n\n"
                f"| 阶段 | 耗时 | 占比 |\n| --- | --- | --- |\n"
                f"| 理解需求（LLM） | {e2e['understand']:.0f} ms | {e2e['understand'] / e2e['total'] * 100:.1f}% |\n"
                f"| 精确求解（CP-SAT） | **{e2e['solve']:.0f} ms** | **{ratio:.2f}%** |\n"
                f"| 生成决策说明（LLM） | {e2e['explain']:.0f} ms | {e2e['explain'] / e2e['total'] * 100:.1f}% |\n"
                f"| **合计** | **{e2e['total']:.0f} ms** | 100% |\n"
            )
            print(md[md.index("### 端到端") :])

    md_path = RESULT_DIR / "bench.md"
    header = (
        f"<!-- 由 backend/benchmark/run_bench.py 生成，seed={args.seed}, runs={args.runs} -->\n"
        f"<!-- 生成时间：{time.strftime('%Y-%m-%d %H:%M')} -->\n\n"
    )
    md_path.write_text(header + md + "\n", encoding="utf-8")
    print(f"汇总已写入 {md_path.relative_to(_BACKEND)}")
    print()
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
