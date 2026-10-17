"""FastAPI 应用入口。

    cd backend
    ..\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000

接口：
    GET  /api/health         健康检查 + 模型配置状态
    POST /api/agent/run      自然语言 → 完整决策流程（需要 LLM）
    POST /api/solver/solve   结构化参数 → 求解（不需要 LLM，可离线测试）
    GET  /api/meta/rules     规则说明（供前端渲染图例）
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import AgentRun, DecisionAgent
from app.llm import LLMClient, LLMError
from app.schemas import SchedulingProblem, SolveResult, WhatIfResult
from app.solver.scheduling import RULE_LABELS, render_schedule_table, solve

# --------------------------------------------------------------------------- #
# 读取 backend/.env
# --------------------------------------------------------------------------- #

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv 未安装时静默跳过
    pass

app = FastAPI(
    title="智能运筹决策 Agent",
    description="大模型驱动：自然语言 → 数学模型 → 精确求解 → 决策说明",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 比赛演示阶段放开；部署时改成前端域名
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_agent = DecisionAgent(LLMClient())

# --------------------------------------------------------------------------- #
# 前端静态页面（Vue 3 单文件，无需 npm 构建；Vue 已内置在 vendor/ 下，离线可用）
# --------------------------------------------------------------------------- #

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------- #
# 请求 / 响应模型
# --------------------------------------------------------------------------- #


class AgentRequest(BaseModel):
    text: str = Field(description="用户用中文描述的排班需求")
    skip_narrative: bool = Field(default=False, description="跳过最后的自然语言说明（省 token）")


class SolveRequest(BaseModel):
    problem: SchedulingProblem
    time_limit: float = Field(default=15.0, ge=1.0, le=120.0)


class WhatIfRequest(BaseModel):
    problem: SchedulingProblem = Field(description="当前方案对应的问题参数（用 /api/agent/run 返回的 problem）")
    text: str = Field(description="假设，例如「如果张伟周三请假会怎样」")
    skip_narrative: bool = Field(default=False, description="跳过最后的自然语言说明（省 token）")


class SolveResponse(BaseModel):
    result: SolveResult
    schedule_table_md: str


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health() -> dict[str, object]:
    client = LLMClient()
    return {
        "status": "ok",
        "llm": {
            "configured": bool(client.api_key),
            "mock": os.getenv("LLM_MOCK", "").lower() in {"1", "true", "yes", "on"},
            "base_url": client.base_url,
            "model": client.model,
        },
        "solver": "OR-Tools CP-SAT",
    }


@app.get("/api/meta/rules")
def meta_rules() -> dict[str, object]:
    return {
        "rules": RULE_LABELS,
        "note": "demand / availability / max_shifts / rest_days / max_consecutive 会在无解时被自动松弛。",
    }


@app.post("/api/agent/run", response_model=AgentRun)
async def agent_run(req: AgentRequest) -> AgentRun:
    try:
        return await _agent.run(req.text, skip_narrative=req.skip_narrative)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/solver/solve", response_model=SolveResponse)
def solver_solve(req: SolveRequest) -> SolveResponse:
    try:
        result = solve(req.problem, time_limit=req.time_limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"建模失败：{exc}") from exc
    table = render_schedule_table(req.problem, result) if result.assignments else ""
    return SolveResponse(result=result, schedule_table_md=table)


@app.post("/api/agent/whatif", response_model=WhatIfResult)
async def agent_whatif(req: WhatIfRequest) -> WhatIfResult:
    """「如果……会怎样」：解析假设 → 应用变更 → 重解 → 方案对比。"""
    try:
        return await _agent.whatif(req.problem, req.text, skip_narrative=req.skip_narrative)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
