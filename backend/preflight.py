"""验证：同一段文本跑 3 次，抽取结果是否稳定。

演示视频依赖这个稳定性——如果每次跑出来的班次数都不一样，录像时对不上口播稿。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")

from app.agent import DecisionAgent  # noqa: E402

TEXT_A = (
    "我们便利店下周一到周日要排班。一共 7 个店员：张伟、李娜、王强、刘洋、陈静、赵磊、孙悦。"
    "每天分早班和晚班，晚班需要会收银的人。每个班次每天要 2 个人。"
    "每人每周最多上 5 天班，每周至少休息 1 天。张伟下周三请假，李娜周六希望休息。"
)
TEXT_B = (
    "我们便利店下周一到周日要排班。一共 7 个店员：张伟、李娜、王强、刘洋、陈静、赵磊、孙悦。"
    "每天分早班和晚班，晚班需要会收银的人。这次每个班次每天要 3 个人。"
    "每人每周最多上 5 天班，每周至少休息 1 天。张伟下周三请假。"
)


async def run_case(label: str, text: str, expect: str, runs: int = 3) -> bool:
    print(f"\n{'=' * 66}\n{label}\n{'=' * 66}")
    agent = DecisionAgent()
    ok = True
    for i in range(1, runs + 1):
        run = await agent.run(text, skip_narrative=True)
        if not run.ok or not run.result:
            print(f"  第 {i} 次: ❌ {run.error[:80]}")
            ok = False
            continue
        res = run.result
        shifts = len(res.assignments)
        names_ok = all(a.employee_name != "" for a in res.assignments)
        # 检查是否出现「员工2」这类编造名
        fake = [a.employee_name for a in res.assignments if a.employee_name.startswith("员工")]
        status_ok = res.status == expect
        if not status_ok:
            ok = False
        print(
            f"  第 {i} 次: 状态={res.status:11} 排班={shifts:3} 条  "
            f"耗时={res.solve_time_seconds:.2f}s  "
            f"{'✅' if status_ok else '❌ 期望 ' + expect}  "
            f"{'编造名:' + str(set(fake)) if fake else '姓名正常'}"
        )
    return ok


async def main() -> None:
    a = await run_case("场景 A：应稳定为 28 条 / OPTIMAL", TEXT_A, "OPTIMAL")
    b = await run_case("场景 B：应稳定为 INFEASIBLE", TEXT_B, "INFEASIBLE")
    print(f"\n{'=' * 66}")
    print("总结果:", "✅ 抽取稳定，可用于录像" if (a and b) else "⚠️ 存在不稳定，录像前需多跑几次确认")


if __name__ == "__main__":
    asyncio.run(main())
