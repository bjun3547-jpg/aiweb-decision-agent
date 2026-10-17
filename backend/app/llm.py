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
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

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
                obj = schema_model.model_validate(MOCK_EXTRACTION)
                return obj, json.dumps(MOCK_EXTRACTION, ensure_ascii=False, indent=2)

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
            return MOCK_NARRATIVE
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
