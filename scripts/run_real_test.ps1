# ============================================================
#  真实模式冒烟测试：填 Key → 跑 smoke_test.py --real
#  用法：整段复制粘贴到 PowerShell 窗口，回车
#  （粘贴不受执行策略限制；也可双击 run_real_test.cmd）
#
#  前提：backend\.env 里的 DEEPSEEK_API_KEY 已填真实 Key
#  费用：跑一次约几分钱（一次完整 Agent 链路 = 2 次 LLM 调用）
# ============================================================

# ---------- 0. 前置检查 ----------
$py = "F:\AIWeb\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "找不到虚拟环境：$py" -ForegroundColor Red
    Write-Host "请先双击 scripts\run_check.cmd 自动创建环境" -ForegroundColor Yellow
    return
}

# ---------- 1. 进入项目根目录 ----------
Set-Location F:\AIWeb
Write-Host "[1/4] 当前目录：$(Get-Location)" -ForegroundColor Cyan

# ---------- 2. 从模板创建 .env（已存在就不动，避免冲掉已填的 Key）----------
$envF = "F:\AIWeb\backend\.env"
if (-not (Test-Path $envF)) {
    Copy-Item "F:\AIWeb\backend\.env.example" $envF
    Write-Host "[2/4] 已从模板创建 backend\.env" -ForegroundColor Yellow
} else {
    Write-Host "[2/4] backend\.env 已存在，保留原文件（不覆盖）" -ForegroundColor Green
}

# ---------- 3. 没填 Key 就打开记事本 ----------
if ((Get-Content $envF -Raw) -match "sk-your-key-here") {
    Write-Host "[3/4] 还是占位符 → 打开记事本，请把 DEEPSEEK_API_KEY= 后面换成真实 Key" -ForegroundColor Yellow
    Write-Host "      保存后关闭记事本，脚本会自动继续" -ForegroundColor Gray
    Write-Host "      Key 申请：https://platform.deepseek.com/api_keys" -ForegroundColor Gray
    Start-Process -FilePath notepad.exe -ArgumentList $envF -Wait
} else {
    Write-Host "[3/4] Key 已填写" -ForegroundColor Green
}

# ---------- 4. 二次校验，仍是占位符就中止 ----------
if ((Get-Content $envF -Raw) -match "sk-your-key-here") {
    Write-Host ""
    Write-Host "Key 仍是占位符，已中止。真实模式必须要有 Key。" -ForegroundColor Red
    Write-Host "临时想看效果，可以先跑离线模式：" -ForegroundColor Yellow
    Write-Host "  cd F:\AIWeb\backend; ..\.venv\Scripts\python.exe smoke_test.py" -ForegroundColor Yellow
    return
}
Write-Host "[4/4] 校验通过，开始真实调用 DeepSeek（约几分钱）" -ForegroundColor Green
Write-Host ""

# ---------- 5. 进 backend 跑真实冒烟测试 ----------
# 注意：smoke_test.py 即使 LLM 调用失败，退出码仍是 0，
#      所以不能只看 $LASTEXITCODE，要解析输出里的 ok: True / False
Set-Location F:\AIWeb\backend
$null = & $py smoke_test.py --real 2>&1 | Tee-Object -Variable log
$text = ($log | Out-String)

# ---------- 收尾：按输出内容判断真实结果 ----------
Write-Host ""
if ($text -match "ok: True") {
    Write-Host "真实链路验证通过：LLM 调用成功，Agent 六步全部跑通。" -ForegroundColor Green
    Write-Host "接下来可以启动服务看页面了：" -ForegroundColor Green
    Write-Host "  cd F:\AIWeb\backend; ..\.venv\Scripts\uvicorn.exe app.main:app --port 8000" -ForegroundColor Gray
} elseif ($text -match "401") {
    Write-Host "失败：API Key 无效（HTTP 401）。" -ForegroundColor Red
    Write-Host "  → 检查 backend\.env 里 Key 有没有多余空格、引号，或已被删除" -ForegroundColor Yellow
    Write-Host "  → 去 https://platform.deepseek.com/api_keys 重新复制一次" -ForegroundColor Yellow
} elseif ($text -match "402") {
    Write-Host "失败：账户余额不足（HTTP 402）。" -ForegroundColor Red
    Write-Host "  → 去 https://platform.deepseek.com/top_up 充值，充 10 元够跑完全程" -ForegroundColor Yellow
} elseif ($text -match "Timeout|超时|ConnectError") {
    Write-Host "失败：网络连不上 api.deepseek.com。" -ForegroundColor Red
    Write-Host "  → 浏览器打开 https://api.deepseek.com 看是否能访问" -ForegroundColor Yellow
    Write-Host "  → 关掉代理/VPN 再试一次" -ForegroundColor Yellow
} else {
    Write-Host "结果不确定，请把上面完整输出截图发我。" -ForegroundColor Red
}
