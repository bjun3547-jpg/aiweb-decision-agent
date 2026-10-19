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
import time
from pathlib import Path

import httpx
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
# 账户余额查询（带缓存，失败不影响健康检查）
# --------------------------------------------------------------------------- #

_BALANCE_TTL_SECONDS = 60.0
_BALANCE_TIMEOUT_SECONDS = 2.5

# (缓存时间戳, 缓存结果)
_balance_cache: tuple[float, dict[str, object]] | None = None


def _fetch_balance(base_url: str, api_key: str) -> dict[str, object]:
    """查询大模型账户余额。任何异常都转成 `{ok: False, error: ...}`，绝不抛出。

    金额一律返回字符串（接口原始就是字符串），避免浮点误差；
    Key 只回显前 5 位，其余打码，防止通过接口泄露。
    """
    global _balance_cache

    now = time.monotonic()
    if _balance_cache is not None and now - _balance_cache[0] < _BALANCE_TTL_SECONDS:
        return _balance_cache[1]

    result: dict[str, object]
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/user/balance",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_BALANCE_TIMEOUT_SECONDS,
        )
        if resp.status_code == 401:
            result = {"ok": False, "error": "API Key 无效（HTTP 401）"}
        elif resp.status_code == 404:
            result = {"ok": False, "error": "当前服务商不支持余额查询接口"}
        elif resp.status_code != 200:
            result = {"ok": False, "error": f"余额接口返回 HTTP {resp.status_code}"}
        else:
            data = resp.json()
            infos = data.get("balance_infos") or [{}]
            first = infos[0] or {}
            result = {
                "ok": True,
                "available": bool(data.get("is_available")),
                "currency": first.get("currency", "CNY"),
                "total": first.get("total_balance"),
                "topped_up": first.get("topped_up_balance"),
                "granted": first.get("granted_balance"),
                "key_hint": f"{api_key[:5]}***",
            }
    except httpx.TimeoutException:
        result = {"ok": False, "error": f"查询余额超时（>{_BALANCE_TIMEOUT_SECONDS}s）"}
    except Exception as exc:  # noqa: BLE001 - 余额查询失败不能影响健康检查
        result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    _balance_cache = (now, result)
    return result


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health(skip_balance: bool = False) -> dict[str, object]:
    """健康检查 + 模型配置状态 + 账户余额。

    余额查询有三条保护，避免拖慢页面首屏：
      1. 结果缓存 60 秒，不会每次请求都打外部接口
      2. 超时只等 2.5 秒，网络不通时不会把 /api/health 卡死
      3. 查询失败只返回 error 字段，不影响 status 仍为 ok

    不需要余额时传 `?skip_balance=true`，页面首屏可以用它跳过。
    """
    client = LLMClient()
    mock = os.getenv("LLM_MOCK", "").lower() in {"1", "true", "yes", "on"}

    llm_info: dict[str, object] = {
        "configured": bool(client.api_key),
        "mock": mock,
        "base_url": client.base_url,
        "model": client.model,
    }

    if mock:
        llm_info["balance"] = {"ok": False, "error": "离线样例模式，不查询余额"}
    elif not client.api_key:
        llm_info["balance"] = {"ok": False, "error": "未配置 API Key"}
    elif skip_balance:
        llm_info["balance"] = {"ok": False, "error": "本次请求跳过了余额查询"}
    else:
        llm_info["balance"] = _fetch_balance(client.base_url, client.api_key)

    return {
        "status": "ok",
        "llm": llm_info,
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
