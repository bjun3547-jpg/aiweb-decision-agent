# -*- coding: utf-8 -*-
"""30 题评测：建模成功率 + 冲突诊断命中率。

放在 backend/benchmark/ 下，在 backend/ 目录运行：
    ..\\.venv\\Scripts\\python.exe benchmark\\run_eval.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
if not (ROOT / "app").is_dir():
    ROOT = Path(os.environ.get("AIWEB_BACKEND", ROOT))
sys.path.insert(0, str(ROOT))

from app.schemas import SchedulingProblem  # noqa: E402
from app.solver.scheduling import solve  # noqa: E402


def main() -> None:
    cases_path = Path(__file__).resolve().parent / "cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    rows = []
    fe_total = fe_ok = 0
    conf_total = conf_hit = 0

    for c in cases:
        problem = SchedulingProblem.model_validate(c["problem"])
        expect = c["expect"]
        result = solve(problem, time_limit=10.0)

        if expect["status"] == "OPTIMAL":
            fe_total += 1
            ok = result.status in ("OPTIMAL", "FEASIBLE")
            fe_ok += ok
            rows.append((c["id"], c["kind"], "OPTIMAL", result.status, "OK" if ok else "MISS"))
        else:
            conf_total += 1
            got = sorted(x.rule for x in result.conflicts)
            exp = sorted(expect["conflicts"])
            hit = result.status == "INFEASIBLE" and got == exp
            conf_hit += hit
            rows.append((c["id"], c["kind"], str(exp), str(got), "OK" if hit else "MISS"))

    print("逐题结果：")
    print(f"{'ID':<5}{'类型':<10}{'期望':<24}{'实际':<24}{'判定'}")
    for r in rows:
        print(f"{r[0]:<5}{r[1]:<10}{r[2]:<24}{r[3]:<24}{r[4]}")

    print()
    print("=" * 64)
    print("表 9（直接填进 ch7-测试与部署.md）：")
    print(f"| 建模成功率（24 道可行题） | 可行题求出 OPTIMAL / 24 题 | {fe_ok}/{fe_total} = {fe_ok/fe_total*100:.1f}% |")
    print(f"| 冲突诊断命中率（6 道冲突题） | 正确诊断冲突规则 / 6 题 | {conf_hit}/{conf_total} = {conf_hit/conf_total*100:.1f}% |")


if __name__ == "__main__":
    main()
