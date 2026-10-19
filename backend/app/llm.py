"""LLM 适配层。

设计要点：
- **可替换**：换模型只改 `base_url` / `model`，不动上层代码。
- **结构化输出**：强制 JSON + Pydantic 校验，校验失败自动带着报错重试一次。
  这是「防止 LLM 幻觉污染数学模型」的第一道闸门。
- **离线可测**：`LLM_MOCK=1` 时返回固定样例，方便队友在没有 API Key 的机器上开发前端。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys as _sys
import time
from pathlib import Path as _Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

# 允许 `python app/llm.py` 直接运行自检：把 backend/ 加进模块搜索路径。
# 正常 `python -m app.llm` 时 __package__ 是 "app"，这段不执行。
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、格式）。"""


# --------------------------------------------------------------------------- #
# Mock 数据：前端联调 / 无 Key 环境使用
# --------------------------------------------------------------------------- #

MOCK_EXTRACTION: dict[str, Any] = {
    "summary": "7 名店员，每天早班和晚班各需 2 人，每人每周最多上 5 天、至少休 1 天。",
    "problem": {
        "scenario": "scheduling",
        "title": "便利店下周排班",
        "dates": [f"2026-10-{d:02d}" for d in range(19, 26)],
        "employees": [
            {"id": "E01", "name": "张伟", "skills": ["收银", "理货"], "max_shifts": 5,
             "unavailable_dates": ["2026-10-21"], "preferred_off_dates": []},
            {"id": "E02", "name": "李娜", "skills": ["收银"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": ["2026-10-24"]},
            {"id": "E03", "name": "王强", "skills": ["理货"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": []},
            {"id": "E04", "name": "刘洋", "skills": ["收银", "理货"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": []},
            {"id": "E05", "name": "陈静", "skills": ["收银"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": []},
            {"id": "E06", "name": "赵磊", "skills": ["理货"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": ["2026-10-19"]},
            {"id": "E07", "name": "孙悦", "skills": ["收银", "理货"], "max_shifts": 5,
             "unavailable_dates": [], "preferred_off_dates": []},
        ],
        "shifts": [
            {"id": "morning", "name": "早班", "required_skill": None, "start_hour": 8, "end_hour": 16},
            {"id": "evening", "name": "晚班", "required_skill": "收银", "start_hour": 16, "end_hour": 24},
        ],
        "demand": [
            {"date": f"2026-10-{d:02d}", "shift_id": sid, "headcount": 2}
            for d in range(19, 26)
            for sid in ("morning", "evening")
        ],
        "rules": {
            "min_rest_days_per_week": 1,
            "max_consecutive_work_days": 5,
            "max_shifts_per_employee": 5,
            "allow_understaffing": False,
        },
        "source_text": "mock",
    },
    "uncertainties": [
        {
            "field": "shifts.evening.required_skill",
            "question": "晚班是否确实需要具备「收银」技能的员工？",
            "assumption": "已按需要收银技能处理",
            "confidence": 0.55,
        }
    ],
}

MOCK_NARRATIVE = (
    "已根据你的描述排出下周方案。7 名店员共承担 28 个班次，人均 4 天，"
    "最长连续工作 4 天，未触发连续工作上限。张伟 10/21 的请假已避开，"
    "李娜 10/24、赵磊 10/19 的休息偏好均已满足。"
)

MOCK_WHATIF: dict[str, Any] = {
    "understood": "假设张伟下周只能上 2 天班（比原来少 2 天）",
    "ops": [
        {
            "op": "set_max_shifts",
            "employee_id": "E01",
            "employee_name": "张伟",
            "value": 2,
            "reason": "张伟下周只能上 2 天班",
        }
    ],
}

MOCK_WHATIF_NARRATIVE = (
    "这个假设能排出来，但要多挪动几个班次：张伟周四原本有一个班，需要由别人顶上，"
    "店里所有人都还在每周 5 天的上限之内。提醒店长确认周四顶班的人是否熟悉收银。"
)


def _mock_enabled() -> bool:
    return os.getenv("LLM_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


class LLMClient:
    """一个轻量的、只依赖 httpx 的 OpenAI 兼容客户端。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or _mock_enabled()

    # -- 底层调用 ---------------------------------------------------------- #

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if _mock_enabled():
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}, "_mock": True}

        if not self.api_key:
            raise LLMError(
                "未配置 API Key。请在 backend/.env 里设置 DEEPSEEK_API_KEY，"
                "或设置 LLM_MOCK=1 使用离线样例数据。"
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 401:
                    raise LLMError("API Key 无效（HTTP 401）。请检查 backend/.env 里的 DEEPSEEK_API_KEY。")
                if resp.status_code == 402:
                    raise LLMError("账户余额不足（HTTP 402）。")
                if resp.status_code >= 500:
                    raise LLMError(f"模型服务端错误 HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise LLMError(f"调用失败 HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                if attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
            except LLMError:
                raise
        raise LLMError(f"网络请求失败：{last_err}")

    # -- 结构化输出 -------------------------------------------------------- #

    async def chat_json(
        self,
        system: str,
        user: str,
        schema_model: type[T],
        *,
        temperature: float = 0.1,
        max_retries: int = 1,
    ) -> tuple[T, str]:
        """让模型输出 JSON 并用 Pydantic 校验；失败时带着报错重试。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in range(max_retries + 1):
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            data = await self._post(payload)

            if _mock_enabled():
                src = MOCK_WHATIF if schema_model.__name__ == "WhatIfPatch" else MOCK_EXTRACTION
                obj = schema_model.model_validate(src)
                return obj, json.dumps(src, ensure_ascii=False, indent=2)

            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            try:
                return schema_model.model_validate_json(content), content
            except ValidationError as exc:
                if attempt >= max_retries:
                    raise LLMError(
                        "模型输出的 JSON 不符合约定的数据结构：\n"
                        f"{exc.errors()[:5]}\n原始输出片段：{content[:500]}"
                    ) from exc
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上面的 JSON 未通过结构校验。请严格按约定的 schema 重新输出，"
                            f"只输出 JSON 本身。错误详情：{json.dumps(exc.errors()[:5], ensure_ascii=False)}"
                        ),
                    }
                )

        raise LLMError("结构化输出重试次数用尽。")

    # -- 纯文本生成 -------------------------------------------------------- #

    async def chat_text(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 900,
    ) -> str:
        if _mock_enabled():
            return MOCK_WHATIF_NARRATIVE if "对比" in system or "假设" in system else MOCK_NARRATIVE
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = await self._post(payload)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


# --------------------------------------------------------------------------- #
# 自检入口：python app/llm.py [--text] [--json]
#
# llm.py 本身是模块，没有入口——直接 `python llm.py` 什么都不会发生，
# 这是正常现象（退出码 0 不代表"跑成功了"，只代表"什么都没干"）。
# 这个 __main__ 块就是给它补一个能真正跑起来的入口，用来单独验证：
#   ① .env 有没有被读到  ② Key 有没有效  ③ 网络通不通
#   ④ 结构化输出的 schema 校验和重试是不是真的在工作
# --------------------------------------------------------------------------- #


def _mask(key: str) -> str:
    if not key:
        return "（未配置）"
    return f"{key[:5]}***（长度 {len(key)}）"


async def _self_check(only: str | None = None) -> int:
    from dotenv import load_dotenv  # 单独跑本文件时 .env 不会自动加载，这里补上

    env_file = _Path(__file__).resolve().parent.parent / ".env"
    loaded = env_file.exists() and bool(load_dotenv(env_file))

    client = LLMClient()
    mock = _mock_enabled()

    print("=" * 62)
    print("llm.py 自检")
    print("=" * 62)
    print(f"配置文件   : {env_file}")
    print(f"             {'[已加载]' if loaded else '[未找到，将只用系统环境变量]'}")
    print(f"base_url   : {client.base_url}")
    print(f"model      : {client.model}")
    print(f"api_key    : {_mask(client.api_key)}")
    print(f"mock 模式  : {'开启（LLM_MOCK=1，不联网）' if mock else '关闭（真实调用）'}")
    print(f"超时       : {client.timeout}s")
    print()

    if not client.enabled:
        print("[FAIL] 既没有 API Key，也没有开 LLM_MOCK。")
        print("       请在 backend/.env 里设置 DEEPSEEK_API_KEY，或设 LLM_MOCK=1 离线自检。")
        return 1

    failed = 0

    # ---- 1) 纯文本生成 -------------------------------------------------- #
    if only in (None, "text"):
        print("[1/2] chat_text()  纯文本生成")
        t0 = time.perf_counter()
        try:
            out = await client.chat_text(
                "你是一名运筹优化顾问。用一句中文回答，不要超过 40 字。",
                "用一句话说明为什么排班问题要用求解器而不是人工排。",
            )
            ms = (time.perf_counter() - t0) * 1000
            print(f"      耗时 {ms:.0f}ms")
            print(f"      输出：{out}")
            print("      结论：通过")
        except Exception as exc:  # noqa: BLE001 - 自检要如实打印任何异常
            failed += 1
            print(f"      失败：{type(exc).__name__}: {exc}")
        print()

    # ---- 2) 结构化输出（含 schema 校验） --------------------------------- #
    if only in (None, "json"):
        from app import prompts
        from app.agent import _date_anchors
        from app.schemas import ExtractionResult

        print("[2/2] chat_json()  结构化输出 + schema 校验")
        t0 = time.perf_counter()
        try:
            obj, raw = await client.chat_json(
                prompts.EXTRACTION_SYSTEM.format(**_date_anchors()),
                prompts.EXTRACTION_USER.format(
                    text="我们店 3 个人，下周一到周日每天早班晚班各 1 人，每人最多上 5 天。"
                ),
                ExtractionResult,
            )
            ms = (time.perf_counter() - t0) * 1000
            p = obj.problem
            print(f"      耗时 {ms:.0f}ms")
            print(f"      原始输出 {len(raw)} 字符 → Pydantic 校验：通过")
            print(f"      解析结果：{len(p.employees)} 人 / {len(p.shifts)} 班次 / {len(p.dates)} 天")
            if p.dates:
                print(f"      日期区间：{p.dates[0]} ~ {p.dates[-1]}")
            print(f"      摘要：{obj.summary}")
            print("      结论：通过")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"      失败：{type(exc).__name__}: {exc}")
        print()

    print("=" * 62)
    print("结论：llm.py 工作正常" if not failed else f"结论：{failed} 项失败，见上方输出")
    print("=" * 62)
    return 0 if not failed else 1


def _main() -> None:
    args = [a for a in _sys.argv[1:] if a.strip()]
    only = None
    if "--text" in args:
        only = "text"
    elif "--json" in args:
        only = "json"
    elif args and args[0] in ("-h", "--help"):
        print("用法：python app/llm.py [--text | --json]")
        print("  （不带参数）完整自检：配置 + 纯文本 + 结构化输出")
        print("  --text      只测纯文本生成")
        print("  --json      只测结构化输出与 schema 校验")
        return
    _sys.exit(asyncio.run(_self_check(only)))


if __name__ == "__main__":
    _main()
