"""评测集跑分器：30 题 → 参数抽取准确率 / 冲突诊断命中率 / 数字幻觉检出。

与 run_bench.py 的分工：
    run_bench.py  测「求解器多快、方案多好」——三档规模、同一实例重复求解，不调模型。
    run_cases.py  测「模型听不听话」——30 道自然语言题，逐字段比对标准答案。

用法：
    cd backend

    # ① 离线自证：只跑求解器，不调模型，不花钱
    ..\\.venv\\Scripts\\python.exe benchmark\\run_cases.py --validate

    # ② 正式跑分：真实调用大模型（会产生费用）
    ..\\.venv\\Scripts\\python.exe benchmark\\run_cases.py --run

    # ③ 只跑几题试试（省钱）
    ..\\.venv\\Scripts\\python.exe benchmark\\run_cases.py --run --limit 3
    ..\\.venv\\Scripts\\python.exe benchmark\\run_cases.py --run --only R01 C04

产物：
    benchmark/results/report.csv   每题明细，可直接导入 Excel
    benchmark/results/report.md    汇总指标，可直接粘进第 7 章表 9

为什么要有 --validate：
    标准答案如果是手写拍脑袋的，跑分就没有意义——你无法区分「模型答错」和
    「答案本身写错」。所以先让 expect 展开成 SchedulingProblem 直接喂求解器：
    常规/边界题必须 OPTIMAL，冲突题必须报出期望的根因规则。
    这一步不调模型、不花钱，能在几秒内把「标准答案写错」这类错误全部挤出来。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# .env 只有 uvicorn 启动时才会自动加载，脚本单跑要自己补
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
except ImportError:
    pass

from app.schemas import (  # noqa: E402
    DemandEntry,
    Employee,
    SchedulingProblem,
    SchedulingRules,
    ShiftType,
)
from app.solver import capacity_analysis, solve  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases.json"
RESULT_DIR = Path(__file__).resolve().parent / "results"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# 比对字段：10 项，分母统一，这样「参数抽取准确率」是各题可比的
SCORE_FIELDS = [
    "employee_count",
    "employee_names",
    "skills",
    "shift_count",
    "shift_hours",
    "shift_skill",
    "dates",
    "demand",
    "rules",
    "unavailable",
]


# --------------------------------------------------------------------------- #
# 日期锚点：与 agent._date_anchors() 同一口径，题面里的「下周」才能对上
# --------------------------------------------------------------------------- #


def anchors(today: _date | None = None) -> dict[str, str]:
    today = today or _date.today()
    this_monday = today - _timedelta(days=today.weekday())
    next_monday = this_monday + _timedelta(days=7)
    return {
        "today": today.isoformat(),
        "this_monday": this_monday.isoformat(),
        "next_monday": next_monday.isoformat(),
        "next_sunday": (next_monday + _timedelta(days=6)).isoformat(),
    }


def plan_dates(days: int, today: _date | None = None) -> list[str]:
    """计划期 = 下周一起的连续 days 天。"""
    start = _date.fromisoformat(anchors(today)["next_monday"])
    return [(start + _timedelta(days=i)).isoformat() for i in range(days)]


def resolve_unavailable(spec: dict[str, list[str]], days: int, today: _date | None = None) -> dict[str, list[str]]:
    """把 {'张伟': ['wed']} 解析成 {'张伟': ['2026-10-28']}。

    用星期名而不是日序号，是因为题面里人说的是「周三」；
    写死日期会让评测集隔天就失效。
    """
    dates = plan_dates(days, today)
    out: dict[str, list[str]] = {}
    for name, tokens in spec.items():
        resolved: list[str] = []
        for tok in tokens:
            idx = WEEKDAYS.index(tok)
            if idx >= len(dates):
                raise ValueError(f"员工 {name} 的不可用日 {tok} 超出 {days} 天的计划期")
            resolved.append(dates[idx])
        out[name] = sorted(resolved)
    return out


# --------------------------------------------------------------------------- #
# 标准答案 → SchedulingProblem
# --------------------------------------------------------------------------- #


def expand(case: dict[str, Any], presets: dict[str, list[dict]], defaults: dict, today: _date | None = None) -> SchedulingProblem:
    """把题库里的一行紧凑描述展开成完整的 SchedulingProblem。

    这一步就是「标准答案」的定义。它必须是纯函数——同样的输入永远得到同样的题，
    否则评测集不可复现，第 7 章的数字就站不住。
    """
    days = int(case["days"])
    dates = plan_dates(days, today)

    unavailable = resolve_unavailable(case.get("unavailable", {}), days, today)

    employees = [
        Employee(
            id=f"E{i + 1:02d}",
            name=name,
            skills=list(case.get("skills", {}).get(name, [])),
            unavailable_dates=unavailable.get(name, []),
        )
        for i, name in enumerate(case["employees"])
    ]

    shifts = [ShiftType(**s) for s in presets[case["shifts"]]]

    per_shift: dict[str, int] = case["demand"]
    demand = [
        DemandEntry(date=d, shift_id=s.id, headcount=int(per_shift[s.id]))
        for d in dates
        for s in shifts
    ]

    rules = SchedulingRules(**{**defaults, **case.get("rules", {})})

    return SchedulingProblem(
        title=case["title"],
        dates=dates,
        employees=employees,
        shifts=shifts,
        demand=demand,
        rules=rules,
        source_text=case["text"],
    )


# --------------------------------------------------------------------------- #
# 逐字段比对
# --------------------------------------------------------------------------- #


def _skill_map(p: SchedulingProblem) -> dict[str, set[str]]:
    return {e.name: set(e.skills) for e in p.employees}


def _shift_by_name(p: SchedulingProblem) -> dict[str, ShiftType]:
    return {s.name: s for s in p.shifts}


def _demand_map(p: SchedulingProblem) -> dict[tuple[str, str], int]:
    """(日期, 班次名) → 需求人数。

    用班次名而不是 shift_id：模型给 id 起什么名字我们不该管，
    「早班」这个名字是用户在题面里说的，才是可比的口径。
    """
    name_of = {s.id: s.name for s in p.shifts}
    out: dict[tuple[str, str], int] = {}
    for d in p.demand:
        key = (d.date, name_of.get(d.shift_id, d.shift_id))
        out[key] = out.get(key, 0) + int(d.headcount)
    return out


def _unavail_map(p: SchedulingProblem) -> dict[str, set[str]]:
    return {e.name: set(e.unavailable_dates) for e in p.employees}


def compare(expect: SchedulingProblem, actual: SchedulingProblem) -> dict[str, tuple[bool, str]]:
    """返回 字段 → (是否通过, 不一致的说明)。"""
    res: dict[str, tuple[bool, str]] = {}

    # 1. 员工人数
    ok = len(expect.employees) == len(actual.employees)
    res["employee_count"] = (ok, f"{len(actual.employees)} vs 期望 {len(expect.employees)}")

    # 2. 员工姓名集合
    en = {e.name for e in expect.employees}
    an = {e.name for e in actual.employees}
    res["employee_names"] = (en == an, "" if en == an else f"缺 {sorted(en - an)} 多 {sorted(an - en)}")

    # 3. 逐人技能
    es, as_ = _skill_map(expect), _skill_map(actual)
    diff = [f"{k}:{sorted(as_.get(k, set()))}≠{sorted(v)}" for k, v in es.items() if as_.get(k, set()) != v]
    res["skills"] = (not diff, "; ".join(diff[:4]))

    # 4. 班次类型数
    ok = len(expect.shifts) == len(actual.shifts)
    res["shift_count"] = (ok, f"{len(actual.shifts)} vs 期望 {len(expect.shifts)}")

    # 5/6. 每班的起止小时与技能要求（按班次名对齐）
    esh, ash = _shift_by_name(expect), _shift_by_name(actual)
    bad_hours = [
        f"{n}:{ash[n].start_hour}-{ash[n].end_hour}≠{s.start_hour}-{s.end_hour}"
        for n, s in esh.items()
        if n not in ash or (ash[n].start_hour, ash[n].end_hour) != (s.start_hour, s.end_hour)
    ]
    res["shift_hours"] = (not bad_hours, "; ".join(bad_hours) or ("缺班次" if len(ash) != len(esh) else ""))

    bad_skill = [
        f"{n}:{ash[n].required_skill}≠{s.required_skill}"
        for n, s in esh.items()
        if n not in ash or ash[n].required_skill != s.required_skill
    ]
    res["shift_skill"] = (not bad_skill, "; ".join(bad_skill))

    # 7. 日期列表
    ed, ad = sorted(expect.dates), sorted(actual.dates)
    res["dates"] = (ed == ad, "" if ed == ad else f"{ad[:2]}…({len(ad)} 天) vs 期望 {ed[:2]}…({len(ed)} 天)")

    # 8. 需求（按 日期×班次名 对齐，缺失的 0 视为 0，多出来的算错）
    em, am = _demand_map(expect), _demand_map(actual)
    keys = set(em) | set(am)
    bad_demand = [f"{d}/{s}:{am.get((d, s), 0)}≠{em.get((d, s), 0)}" for (d, s) in keys if am.get((d, s), 0) != em.get((d, s), 0)]
    res["demand"] = (not bad_demand, f"{len(bad_demand)} 处不一致: " + "; ".join(bad_demand[:3]))

    # 9. 全局规则
    er, ar = expect.rules.model_dump(), actual.rules.model_dump()
    bad_rules = [f"{k}:{ar[k]}≠{er[k]}" for k in er if ar.get(k) != er[k]]
    res["rules"] = (not bad_rules, "; ".join(bad_rules))

    # 10. 请假日期
    eu, au = _unavail_map(expect), _unavail_map(actual)
    bad_un = [f"{k}:{sorted(au.get(k, set()))}≠{sorted(v)}" for k, v in eu.items() if au.get(k, set()) != v]
    res["unavailable"] = (not bad_un, "; ".join(bad_un[:3]))

    return res


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #


@dataclass
class Loaded:
    cases: list[dict[str, Any]] = field(default_factory=list)
    presets: dict[str, list[dict]] = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)


def load_cases() -> Loaded:
    raw = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return Loaded(cases=raw["cases"], presets=raw["shift_presets"], defaults=raw["default_rules"])


# --------------------------------------------------------------------------- #
# 模式一：离线自证（不调模型）
# --------------------------------------------------------------------------- #


def do_validate(loaded: Loaded, today: _date | None = None) -> int:
    print("=" * 78)
    print("离线自证：把标准答案直接喂求解器，检查题目本身写得对不对")
    print("=" * 78)
    print(f"日期锚点：下周 {anchors(today)['next_monday']} ~ {anchors(today)['next_sunday']}")
    print()

    fails = 0
    rows = []
    for case in loaded.cases:
        problem = expand(case, loaded.presets, loaded.defaults, today)
        result = solve(problem, time_limit=20.0)

        exp_status = case["expect"]["status"]
        exp_rules = case["expect"]["conflict_rules"]
        got_rules = [c.rule for c in result.conflicts]

        problems: list[str] = []
        if exp_status is not None and result.status != exp_status:
            problems.append(f"状态 {result.status} ≠ 期望 {exp_status}")
        missing = [r for r in exp_rules if r not in got_rules]
        if missing:
            problems.append(f"未报出根因 {missing}（实际 {got_rules}）")

        # 常规题必须真的排满需求，防止「OPTIMAL 但少排了人」
        total_demand = sum(d.headcount for d in problem.demand)
        if exp_status == "OPTIMAL" and len(result.assignments) != total_demand:
            problems.append(f"排班 {len(result.assignments)} ≠ 需求 {total_demand}")

        ok = not problems
        if not ok:
            fails += 1

        # 用求解器自己的产能口径，别在这里另写一套算术——
        # 自己算的「产能」漏掉「请假只占用可用天数、不直接扣上限」这层，
        # 曾把一道其实可行的题误判成不可行。
        ca = capacity_analysis(problem)
        cap = ca["total_capacity"]
        rows.append((case["id"], case["type"], problem, result, total_demand, cap, ok, problems))

        print(
            f"{'✅' if ok else '❌'} {case['id']:4} {case['title'][:30]:<32}"
            f" 需求{total_demand:>3} 产能{cap:>3} | {result.status:<8}"
            f" 排班{len(result.assignments):>3} 冲突{got_rules if got_rules else '—'}"
        )
        for p in problems:
            print(f"        ↳ {p}")

    print()
    print(f"共 {len(loaded.cases)} 题，通过 {len(loaded.cases) - fails}，失败 {fails}")
    if fails:
        print("⚠️ 标准答案本身有问题，先修好再跑模型——否则分不清是模型错还是答案错。")
        return 1
    print("✅ 标准答案全部自证通过，可以进入 --run 阶段。")
    return 0


# --------------------------------------------------------------------------- #
# 模式二：真实跑分（调模型）
# --------------------------------------------------------------------------- #


async def do_run(loaded: Loaded, only: list[str], limit: int | None, narrative: bool, today: _date | None = None) -> int:
    from app.agent import DecisionAgent
    from app.llm import LLMClient

    cases = loaded.cases
    if only:
        cases = [c for c in cases if c["id"] in only]
    if limit:
        cases = cases[:limit]

    llm = LLMClient()
    if not llm.enabled:
        print("❌ 未配置 DEEPSEEK_API_KEY，无法跑分。先跑 --validate 或检查 backend/.env")
        return 1

    print("=" * 78)
    print(f"正式跑分：{len(cases)} 题，模型 {llm.model}，说明生成 {'开' if narrative else '关'}")
    print("=" * 78)

    agent = DecisionAgent(llm)
    rows: list[dict[str, Any]] = []
    t_start = time.time()

    for i, case in enumerate(cases, 1):
        expect = expand(case, loaded.presets, loaded.defaults, today)
        print(f"[{i:>2}/{len(cases)}] {case['id']} {case['title']} … ", end="", flush=True)

        t0 = time.time()
        try:
            run = await agent.run(case["text"], skip_narrative=not narrative)
        except Exception as exc:  # noqa: BLE001 - 单题异常不该中断整轮
            print(f"异常 {type(exc).__name__}: {exc}")
            rows.append(
                {
                    "id": case["id"], "type": case["type"], "title": case["title"],
                    "ok": False, "accuracy": 0.0, "fields_passed": 0, "fields_total": len(SCORE_FIELDS),
                    "failed_fields": "exception", "status_expected": case["expect"]["status"] or "-",
                    "status_actual": "EXCEPTION", "conflict_expected": "|".join(case["expect"]["conflict_rules"]),
                    "conflict_actual": "", "conflict_hit": "", "assignments": 0, "objective": "",
                    "elapsed_ms": int((time.time() - t0) * 1000), "understand_ms": "", "solve_ms": "",
                    "explain_ms": "", "explain_warn": "", "numbers_flagged": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        elapsed_ms = int((time.time() - t0) * 1000)
        steps = {s.key: s.duration_ms for s in run.steps}

        if not run.ok or run.problem is None:
            print(f"失败：{run.error[:60]}")
            rows.append(
                {
                    "id": case["id"], "type": case["type"], "title": case["title"],
                    "ok": False, "accuracy": 0.0, "fields_passed": 0, "fields_total": len(SCORE_FIELDS),
                    "failed_fields": "agent_error", "status_expected": case["expect"]["status"] or "-",
                    "status_actual": "AGENT_ERROR", "conflict_expected": "|".join(case["expect"]["conflict_rules"]),
                    "conflict_actual": "", "conflict_hit": "", "assignments": 0, "objective": "",
                    "elapsed_ms": elapsed_ms, "understand_ms": steps.get("understand", ""),
                    "solve_ms": steps.get("solve", ""), "explain_ms": steps.get("explain", ""),
                    "explain_warn": "", "numbers_flagged": "", "error": run.error[:200],
                }
            )
            continue

        checks = compare(expect, run.problem)
        passed = [k for k, (ok, _) in checks.items() if ok]
        failed = [k for k, (ok, _) in checks.items() if not ok]
        accuracy = len(passed) / len(SCORE_FIELDS)

        # 数字护栏的结果只体现在 explain 步骤的 detail 里，单独捞出来存进报告，
        # 否则「数字幻觉检出数」这一行永远填不上。
        explain = next((s for s in run.steps if s.key == "explain"), None)
        explain_warn = bool(explain and explain.status == "warn")
        flagged = 0
        if explain_warn and "来源不明的数字：" in explain.detail:
            body = explain.detail.split("来源不明的数字：", 1)[1].split("（", 1)[0]
            flagged = len([x for x in body.split("、") if x.strip()])

        got_rules = [c.rule for c in (run.result.conflicts if run.result else [])]
        exp_rules = case["expect"]["conflict_rules"]
        conflict_hit = "" if not exp_rules else str(all(r in got_rules for r in exp_rules))

        print(f"抽取 {len(passed)}/{len(SCORE_FIELDS)}  状态 {run.result.status if run.result else '-'}"
              f"  冲突命中 {conflict_hit or '—'}"
              + (f"  未过 {failed}" if failed else ""))

        rows.append(
            {
                "id": case["id"], "type": case["type"], "title": case["title"],
                "ok": True, "accuracy": round(accuracy, 3), "fields_passed": len(passed),
                "fields_total": len(SCORE_FIELDS), "failed_fields": "|".join(failed),
                "status_expected": case["expect"]["status"] or "-",
                "status_actual": run.result.status if run.result else "-",
                "conflict_expected": "|".join(exp_rules), "conflict_actual": "|".join(got_rules),
                "conflict_hit": conflict_hit,
                "assignments": len(run.result.assignments) if run.result else 0,
                "objective": run.result.objective if run.result else "",
                "elapsed_ms": elapsed_ms, "understand_ms": steps.get("understand", ""),
                "solve_ms": steps.get("solve", ""), "explain_ms": steps.get("explain", ""),
                "explain_warn": explain_warn, "numbers_flagged": flagged,
                "error": "",
            }
        )

    write_report(rows, elapsed_ms=int((time.time() - t_start) * 1000), model=llm.model)
    return 0


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


def load_report_csv() -> list[dict[str, Any]]:
    """从已有 report.csv 读回明细，用于「只重出报告、不重跑模型」。

    改一次指标口径就重跑 30 题（3 分半 + 几毛钱）是浪费，
    但数字又必须和报告一致，所以留这条路。
    """
    with (RESULT_DIR / "report.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 标准答案的唯一来源是 cases.json；CSV 只存「跑出来的实际结果」。
    # 期望列若留在 CSV 里，改一次答案就会和题库对不上，所以按题号覆盖。
    expect_by_id = {c["id"]: c["expect"] for c in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]}
    for r in rows:
        r["accuracy"] = float(r["accuracy"] or 0)
        for k in ("fields_passed", "fields_total", "assignments", "elapsed_ms"):
            r[k] = int(float(r[k] or 0))
        r["ok"] = r["ok"] == "True"
        exp = expect_by_id.get(r["id"])
        if exp:
            r["conflict_expected"] = "|".join(exp["conflict_rules"])
            r["status_expected"] = exp["status"] or "-"
            r["conflict_hit"] = (
                "" if not exp["conflict_rules"]
                else str(all(x in (r["conflict_actual"] or "").split("|") for x in exp["conflict_rules"]))
            )
    return rows


def write_report(rows: list[dict[str, Any]], *, elapsed_ms: int, model: str) -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = RESULT_DIR / "report.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    def avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    regular = [r for r in rows if r["type"] != "conflict"]
    conflict = [r for r in rows if r["type"] == "conflict"]

    # JSON 都没解析出来的题，没有「字段」可比。把它算进准确率会同时污染两个数：
    # 既低估了抽取能力，又掩盖了「格式漂移」这个独立问题。所以分开报。
    evaluated = [r for r in rows if r["status_actual"] not in ("AGENT_ERROR", "EXCEPTION")]

    acc = avg([r["accuracy"] for r in evaluated]) * 100
    solved = sum(1 for r in regular if r["status_actual"] == "OPTIMAL")
    hit = sum(1 for r in conflict if r["conflict_hit"] == "True")
    hits_denom = sum(1 for r in conflict if r["conflict_expected"])

    # 精确率要先把 `demand` 剔掉：需求不满足是所有冲突的**共同症状**，
    # 不是根因，留着它会让每道题都「多报一条」，精确率恒为 0，毫无信息量。
    def _specific(s: str) -> set[str]:
        return {x for x in (s or "").split("|") if x and x != "demand"}

    exact = sum(1 for r in conflict if _specific(r["conflict_actual"]) == _specific(r["conflict_expected"]))
    flagged_total = sum(int(r.get("numbers_flagged") or 0) for r in rows)
    warned = sum(1 for r in rows if r.get("explain_warn") in (True, "True"))
    field_acc = {
        f: avg([1.0 if f not in (r["failed_fields"] or "").split("|") else 0.0 for r in evaluated]) * 100
        for f in SCORE_FIELDS
    }

    md = [
        f"<!-- 由 backend/benchmark/run_cases.py 生成，模型 {model} -->",
        f"<!-- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}，总耗时 {elapsed_ms / 1000:.1f} s -->",
        "",
        f"### 评测集指标（{len(rows)} 题）",
        "",
        "| 指标 | 定义 | 结果 |",
        "| --- | --- | --- |",
        f"| JSON 一次通过率 | 抽取阶段一次返回合法 JSON / 总题数 | **{len(evaluated)} / {len(rows)} = {len(evaluated) / max(1, len(rows)) * 100:.0f}%** |",
        f"| 参数抽取准确率 | 逐字段比对通过数 / 总字段数（{len(evaluated)} 题 × {len(SCORE_FIELDS)} 字段，不含 JSON 失败题） | **{acc:.1f}%** |",
        f"| 建模成功率 | 常规+边界题求出 OPTIMAL / {len(regular)} 题 | **{solved} / {len(regular)} = {solved / max(1, len(regular)) * 100:.0f}%** |",
        f"| 冲突诊断命中率 | 设计根因被报出 / {hits_denom} 道冲突题（允许多报） | **{hit} / {hits_denom} = {hit / max(1, hits_denom) * 100:.0f}%** |",
        f"| 冲突诊断精确率 | 除 demand（通用症状）外的规则集合与设计根因完全一致 | **{exact} / {hits_denom} = {exact / max(1, hits_denom) * 100:.0f}%** |",
        f"| 数字幻觉检出 | 数字护栏判定「来源不明」的数字总数 / 触发题数 | **{flagged_total} 个 / {warned} 题** |",
        f"| 端到端平均耗时 | 单题从提交到出结果 | **{avg([r['elapsed_ms'] for r in rows]) / 1000:.1f} s** |",
        "",
        "### 逐字段准确率（看模型最容易错在哪）",
        "",
        "| 字段 | 准确率 |",
        "| --- | --- |",
    ]
    for f, v in sorted(field_acc.items(), key=lambda kv: kv[1]):
        md.append(f"| {f} | {v:.0f}% |")

    md += ["", "### 逐题结果", "", "| 题号 | 类型 | 抽取 | 状态 | 冲突命中 | 未过字段 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        md.append(
            f"| {r['id']} | {r['type']} | {r['fields_passed']}/{r['fields_total']} | "
            f"{r['status_actual']} | {r['conflict_hit'] or '—'} | {r['failed_fields'] or '—'} |"
        )

    (RESULT_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print()
    print(
        f"JSON 一次通过 {len(evaluated)}/{len(rows)} | 抽取准确率 {acc:.1f}% "
        f"| 建模成功 {solved}/{len(regular)} | 冲突命中 {hit}/{hits_denom}（精确 {exact}/{hits_denom}）"
    )
    print(f"明细 {csv_path.relative_to(_BACKEND)}")
    print(f"汇总 {(RESULT_DIR / 'report.md').relative_to(_BACKEND)}")
    print()
    print("\n".join(md[3:12]))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="30 题评测集跑分器")
    parser.add_argument("--validate", action="store_true", help="离线自证标准答案（不调模型、不花钱）")
    parser.add_argument("--run", action="store_true", help="真实调用大模型跑分（会产生费用）")
    parser.add_argument("--only", nargs="*", default=[], help="只跑指定题号，如 --only R01 C04")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    parser.add_argument("--no-narrative", action="store_true", help="跳过决策说明生成（省一半 token）")
    parser.add_argument("--report", action="store_true", help="只从已有 report.csv 重出汇总，不重跑模型")
    args = parser.parse_args()

    if args.report:
        rows = load_report_csv()
        write_report(rows, elapsed_ms=sum(r["elapsed_ms"] for r in rows), model="(读自 report.csv)")
        return 0

    if not args.validate and not args.run:
        parser.print_help()
        return 2

    loaded = load_cases()
    if args.validate:
        return do_validate(loaded)
    return asyncio.run(do_run(loaded, args.only, args.limit, not args.no_narrative))


if __name__ == "__main__":
    sys.exit(main())
