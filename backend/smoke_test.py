"""API 冒烟测试：不启服务器，直接用 TestClient 跑通全链路。

    cd backend
    ..\\.venv\\Scripts\\python.exe smoke_test.py            # 离线 mock 模式，不消耗 token
    ..\\.venv\\Scripts\\python.exe smoke_test.py --real     # 真实调用 LLM（需 .env 里配 Key）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REAL = "--real" in sys.argv
if not REAL:
    os.environ["LLM_MOCK"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

print("=" * 70)
print("0) GET /  （前端页面）")
root = client.get("/")
print("HTTP", root.status_code, "| bytes:", len(root.content), "| 含 Vue:",
      "vue.global.prod.js" in root.text)

print("\n" + "=" * 70)
print("1) GET /api/health")
health = client.get("/api/health").json()
print(json.dumps(health, ensure_ascii=False, indent=2))

print("\n" + "=" * 70)
print("2) POST /api/agent/run")
text = (
    "我们便利店下周一到周日要排班。一共 7 个店员：张伟、李娜、王强、刘洋、陈静、赵磊、孙悦。"
    "每天分早班和晚班，晚班需要会收银的人。每个班次每天要 2 个人。"
    "每人每周最多上 5 天班，每周至少休息 1 天。张伟下周三请假，李娜周六希望休息。"
)
resp = client.post("/api/agent/run", json={"text": text})
print("HTTP", resp.status_code)
data = resp.json()

if "steps" in data:
    print("\n-- Agent 轨迹 --")
    for s in data["steps"]:
        print(f"  [{s['status']:5}] {s['name']:<8} {s['duration_ms']:>5}ms  {s['detail'][:90]}")
    print("\n-- 结果 --")
    print("ok:", data.get("ok"), "| status:", (data.get("result") or {}).get("status"))
    if data.get("warnings"):
        print("warnings:", data["warnings"])
    if data.get("narrative"):
        print("\n决策说明：\n" + data["narrative"])
    if data.get("schedule_table_md"):
        print("\n" + data["schedule_table_md"])
else:
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])

print("\n" + "=" * 70)
print("3) POST /api/solver/solve（纯求解，不经过 LLM）")
from demo_scheduling import build_feasible  # noqa: E402

problem = build_feasible()
r2 = client.post("/api/solver/solve", json={"problem": problem.model_dump(), "time_limit": 10})
print("HTTP", r2.status_code)
if r2.status_code == 200:
    d2 = r2.json()
    print("status:", d2["result"]["status"], "| 排班条数:", len(d2["result"]["assignments"]))
    print(d2["schedule_table_md"].splitlines()[0])
else:
    print(r2.text[:800])

print("\n" + "=" * 70)
print("4) POST /api/solver/solve（约束冲突场景，验证自愈）")
from demo_scheduling import build_infeasible  # noqa: E402

bad = build_infeasible()
r3 = client.post("/api/solver/solve", json={"problem": bad.model_dump(), "time_limit": 10})
if r3.status_code == 200:
    d3 = r3.json()["result"]
    print("status:", d3["status"])
    print("最小冲突规则:", [c["label"] for c in d3["conflicts"]])
    print("松弛后仍违反:", len(d3["violations"]), "类")
    print("仍给出可行方案:", len(d3["assignments"]), "条排班")
else:
    print(r3.text[:800])
