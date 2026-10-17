# ============================================================
#  传智杯 AIWeb · 环境自检脚本
#  用法（三选一，推荐 ②）：
#    ① 双击同目录下的 run_check.cmd（最省事）
#    ② 整段复制本文件内容，粘贴到 PowerShell 窗口，回车
#       （粘贴不受执行策略限制，最稳妥）
#    ③ 右键本文件 →「使用 PowerShell 运行」
#       ⚠ 若报「未对文件进行数字签名」，说明系统执行策略拦截了 .ps1，
#         改用 ① 或 ②，或先执行：
#         Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
#  做五件事：查目录 → 查依赖 → 查 .env → 跑冒烟测试 → 查 Git 状态
#  不联网也能跑（没填 Key 时自动降级为离线模式）
# ============================================================

$ErrorActionPreference = "Continue"
$root = "F:\AIWeb"
$py   = "$root\.venv\Scripts\python.exe"
$envF = "$root\backend\.env"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  传智杯 AIWeb · 环境自检" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---------- 1/5 目录 ----------
Write-Host ""
Write-Host "[1/5] 检查项目目录" -ForegroundColor Cyan
if (Test-Path $root) {
    Set-Location $root
    Write-Host "  OK  当前目录：$(Get-Location)" -ForegroundColor Green
} else {
    Write-Host "  失败：找不到 $root" -ForegroundColor Red
    Write-Host "  请先 clone：git clone https://github.com/bjun3547-jpg/aiweb-decision-agent.git F:\AIWeb" -ForegroundColor Yellow
}

# ---------- 2/5 依赖 ----------
Write-Host ""
Write-Host "[2/5] 检查 Python 环境" -ForegroundColor Cyan
if (Test-Path $py) {
    & $py -c "import ortools, fastapi, httpx, pydantic, dotenv; print('  OK  依赖完整')"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  依赖缺失，正在安装（约 1-3 分钟）..." -ForegroundColor Yellow
        & $py -m pip install -r "$root\backend\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
    }
} else {
    Write-Host "  虚拟环境不存在，正在创建..." -ForegroundColor Yellow
    python -m venv "$root\.venv"
    & $py -m pip install -r "$root\backend\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
}

# ---------- 3/5 .env ----------
Write-Host ""
Write-Host "[3/5] 检查 .env 配置" -ForegroundColor Cyan
if (-not (Test-Path $envF)) {
    Copy-Item "$root\backend\.env.example" $envF
    Write-Host "  已从模板创建 backend\.env" -ForegroundColor Yellow
}

$needKey = (Get-Content $envF -Raw) -match "sk-your-key-here"
if ($needKey) {
    Write-Host "  .env 里还是占位符，需要填真实 Key" -ForegroundColor Yellow
    Write-Host "  即将打开记事本：把 DEEPSEEK_API_KEY= 后面换成你的 Key，保存后关闭记事本" -ForegroundColor Yellow
    Write-Host "  （暂时没有 Key？直接关掉记事本，脚本会自动用离线模式继续）" -ForegroundColor Gray
    Write-Host "  （Key 申请：https://platform.deepseek.com/api_keys）" -ForegroundColor Gray
    Start-Process -FilePath notepad.exe -ArgumentList $envF -Wait
    $needKey = (Get-Content $envF -Raw) -match "sk-your-key-here"
    if ($needKey) {
        Write-Host "  仍未填写 → 本次以离线模式运行" -ForegroundColor Yellow
    } else {
        Write-Host "  OK  Key 已填写" -ForegroundColor Green
    }
} else {
    Write-Host "  OK  Key 已填写" -ForegroundColor Green
}

# ---------- 4/5 冒烟测试 ----------
Write-Host ""
Write-Host "[4/5] 运行 API 冒烟测试" -ForegroundColor Cyan
Set-Location "$root\backend"
if ($needKey) {
    Write-Host "  离线模式（不调用真实 API，不消耗 token）" -ForegroundColor Gray
    & $py smoke_test.py
} else {
    Write-Host "  真实模式（会调用 DeepSeek，约几分钱）" -ForegroundColor Gray
    & $py smoke_test.py --real
}

# ---------- 5/5 Git ----------
Write-Host ""
Write-Host "[5/5] 检查 Git 状态" -ForegroundColor Cyan
Set-Location $root
git status -sb
Write-Host ""
Write-Host "  各成员提交数：" -ForegroundColor Gray
git shortlog -sn --all

# ---------- 收尾 ----------
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  自检结束" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  接下来手动做两件事：" -ForegroundColor Yellow
Write-Host "   1. 若上面显示 main 领先 origin/main → 执行 git push" -ForegroundColor Yellow
Write-Host "   2. 若第 4 步是离线模式 → 填好 Key 后重跑一次：" -ForegroundColor Yellow
Write-Host "      cd F:\AIWeb\backend; ..\.venv\Scripts\python.exe smoke_test.py --real" -ForegroundColor Yellow
Write-Host ""
