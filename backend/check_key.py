"""检查 API Key 是否可用、余额有多少。录制视频前必跑一次。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

env_path = Path(__file__).resolve().parent / ".env"
if not env_path.exists():
    print("❌ backend/.env 不存在，先 Copy-Item .env.example .env")
    sys.exit(1)

raw = env_path.read_text(encoding="utf-8")
m = re.search(r"^\s*DEEPSEEK_API_KEY\s*=\s*(\S+)", raw, re.M)
if not m:
    print("❌ .env 里没有 DEEPSEEK_API_KEY")
    sys.exit(1)

key = m.group(1)
print(f"Key: {key[:5]}*** ({len(key)} 位)")
if "your-key" in key:
    print("❌ 还是示例占位符，没填真实 Key")
    sys.exit(1)

try:
    r = httpx.get(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}"},
        timeout=25,
    )
except Exception as exc:  # noqa: BLE001
    print(f"❌ 网络请求失败：{type(exc).__name__}: {str(exc)[:150]}")
    sys.exit(1)

print(f"余额接口 HTTP {r.status_code}")
if r.status_code == 401:
    print("❌ Key 无效（401）。去 platform.deepseek.com 重新生成")
    sys.exit(1)
if r.status_code != 200:
    print(f"⚠️ 返回：{r.text[:300]}")
    sys.exit(1)

data = r.json()
print("账户可用:", "是 ✅" if data.get("is_available") else "否 ❌（余额为 0，需充值）")
for info in data.get("balance_infos", []):
    print(
        f"  总余额 {info.get('total_balance')} {info.get('currency')}"
        f"  (充值 {info.get('topped_up_balance')} / 赠送 {info.get('granted_balance')})"
    )

if not data.get("is_available"):
    print("\n⚠️ 余额不足，演示会失败。请先充值（10 元足够录完整套视频）")
    sys.exit(1)
print("\n✅ 可以正常调用模型")
