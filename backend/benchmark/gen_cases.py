# -*- coding: utf-8 -*-
"""生成 30 题评测集 cases.json（18 常规 + 6 边界 + 6 冲突）。

用法（在 backend/benchmark/ 下）：
    ..\\.venv\\Scripts\\python.exe benchmark\\gen_cases.py
"""
import json
from pathlib import Path

NAMES = ["张伟", "李娜", "王强", "刘洋", "陈静", "赵磊", "孙悦", "周杰", "吴婷", "徐磊", "孙丽", "马超", "郑浩", "林静"]
DATES7 = [f"2026-10-{d:02d}" for d in range(19, 26)]


def build(n, shifts, demand, skill=False, days=7, per_max=5, min_rest=1, max_consec=6, unavail=None):
    """构造一个结构化排班问题（SchedulingProblem 的字典形式）。"""
    dates = [f"2026-10-{d:02d}" for d in range(19, 19 + days)]
    shift_defs = [{"id": "morning", "name": "早班", "required_skill": None, "start_hour": 8, "end_hour": 16}]
    if shifts == 2:
        shift_defs.append({"id": "evening", "name": "晚班",
                           "required_skill": "收银" if skill else None, "start_hour": 16, "end_hour": 24})

    employees = []
    for i in range(1, n + 1):
        sk = ["收银", "理货"] if skill else ["理货"]
        employees.append({
            "id": f"E{i:02d}",
            "name": NAMES[i - 1],
            "skills": sk,
            "max_shifts": None,
            "unavailable_dates": (unavail or {}).get(i, []),
            "preferred_off_dates": [],
        })

    demand_entries = [
        {"date": d, "shift_id": s["id"], "headcount": demand}
        for d in dates for s in shift_defs
    ]

    return {
        "scenario": "scheduling",
        "title": f"{n} 人 · {shifts} 班 · 每班 {demand} 人",
        "dates": dates,
        "employees": employees,
        "shifts": shift_defs,
        "demand": demand_entries,
        "rules": {
            "min_rest_days_per_week": min_rest,
            "max_consecutive_work_days": max_consec,
            "max_shifts_per_employee": per_max,
            "allow_understaffing": False,
        },
        "source_text": f"{n} 个店员，每天 {shifts} 个班次，每班 {demand} 人，每人最多 {per_max} 天，每周至少休 {min_rest} 天",
    }


cases = []


def add(cid, kind, problem, expect):
    cases.append({"id": cid, "kind": kind, "problem": problem, "expect": expect})


# ---- 18 常规（可行，期望 OPTIMAL）----
add("R01", "regular", build(5, 1, 2), {"status": "OPTIMAL"})
add("R02", "regular", build(6, 2, 1), {"status": "OPTIMAL"})
add("R03", "regular", build(7, 2, 2), {"status": "OPTIMAL"})
add("R04", "regular", build(8, 2, 2), {"status": "OPTIMAL"})
add("R05", "regular", build(10, 2, 2), {"status": "OPTIMAL"})
add("R06", "regular", build(12, 2, 3), {"status": "OPTIMAL"})
add("R07", "regular", build(7, 1, 3), {"status": "OPTIMAL"})
add("R08", "regular", build(6, 2, 2), {"status": "OPTIMAL"})
add("R09", "regular", build(7, 2, 2, skill=True), {"status": "OPTIMAL"})
add("R10", "regular", build(8, 2, 2, skill=True, per_max=6), {"status": "OPTIMAL"})
add("R11", "regular", build(12, 2, 2, skill=True), {"status": "OPTIMAL"})
add("R12", "regular", build(5, 1, 1), {"status": "OPTIMAL"})
add("R13", "regular", build(6, 2, 1, skill=True), {"status": "OPTIMAL"})
add("R14", "regular", build(9, 2, 2, skill=True), {"status": "OPTIMAL"})
add("R15", "regular", build(7, 2, 1), {"status": "OPTIMAL"})
add("R16", "regular", build(8, 1, 4), {"status": "OPTIMAL"})
add("R17", "regular", build(10, 2, 3, skill=True), {"status": "OPTIMAL"})
add("R18", "regular", build(6, 2, 2, max_consec=5), {"status": "OPTIMAL"})

# ---- 6 边界（可行，期望 OPTIMAL）----
add("B01", "boundary", build(1, 1, 1, days=5, per_max=5, min_rest=0), {"status": "OPTIMAL"})
add("B02", "boundary", build(5, 1, 3), {"status": "OPTIMAL"})
add("B03", "boundary", build(3, 1, 1, unavail={1: DATES7}), {"status": "OPTIMAL"})
add("B04", "boundary", build(3, 2, 1, skill=True), {"status": "OPTIMAL"})
add("B05", "boundary", build(6, 1, 3), {"status": "OPTIMAL"})
add("B06", "boundary", build(10, 2, 1, days=14), {"status": "OPTIMAL"})

# ---- 6 冲突（不可行，期望 INFEASIBLE + 规则对）----
add("C01", "conflict", build(7, 2, 3, min_rest=0, max_consec=7), {"status": "INFEASIBLE", "conflicts": ["demand", "max_shifts"]})
add("C02", "conflict", build(3, 1, 3, unavail={1: DATES7}, min_rest=0, max_consec=7, per_max=7), {"status": "INFEASIBLE", "conflicts": ["demand", "availability"]})
add("C03", "conflict", build(5, 1, 2, min_rest=5, max_consec=7, per_max=7), {"status": "INFEASIBLE", "conflicts": ["demand", "rest_days"]})
add("C04", "conflict", build(3, 2, 1, min_rest=0, max_consec=1, per_max=7), {"status": "INFEASIBLE", "conflicts": ["demand", "max_consecutive"]})
add("C05", "conflict", build(5, 1, 1, min_rest=0, max_consec=7, per_max=0), {"status": "INFEASIBLE", "conflicts": ["demand", "max_shifts"]})
add("C06", "conflict", build(2, 1, 2, unavail={1: DATES7}, min_rest=0, max_consec=7, per_max=7), {"status": "INFEASIBLE", "conflicts": ["demand", "availability"]})

out = Path(__file__).resolve().parent / "cases.json"
out.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"OK: {len(cases)} cases -> {out}")
