# 大模型驱动的智能运筹决策 Agent

> 传智杯 · AI WEB 网页开发挑战赛 参赛作品
>
> 用一句中文描述业务排班需求，Agent 自动完成**数学建模 → 精确求解 → 决策解释 → 冲突自愈**。

---

## 1. 它解决什么问题

排班是几乎每家企业都要做、却很少做好的事：店长拿 Excel 手工排，耗时几小时，还经常违反工时规定、漏掉员工请假、忙闲不均。

市面上的 AI 工具有两种极端：

| 做法 | 问题 |
| --- | --- |
| 纯大模型直接「排一个方案」 | 看起来合理，**但算不对**——会漏需求、超工时，无法验证 |
| 传统排班软件 | 能算对，**但要求用户会用建模语言**，业务人员上手困难 |

**本项目的做法：让大模型只做「翻译」，让求解器做「计算」。**

```
用户的中文需求
      │  ① 大模型：理解意图 → 结构化参数（JSON Schema 强约束）
      ▼
   数学模型（决策变量 / 目标函数 / 约束）
      │  ② OR-Tools CP-SAT：精确求解，保证最优性与可验证性
      ▼
   最优排班方案 + 冲突诊断（若不可行则自动定位冲突规则并给出松弛方案）
      │  ③ 大模型：把结果翻译成业务人员看得懂的决策建议
      ▼
   Web 界面：甘特式排班表 / 冲突卡片 / 关键指标
```

这就是所谓的**神经-符号混合（Neuro-Symbolic）**架构：大模型负责语义，求解器负责数学。**结果既好用，又算得对。**

---

## 2. 核心能力

- **自然语言建模** —— 「7 个店员，每天早班晚班各 2 人，每人每周最多 5 天，张伟周三请假」→ 直接出方案
- **精确最优解** —— 用 CP-SAT 求解，不是猜。同一输入永远同一输出，可复现
- **不可行自愈** —— 约束冲突时，用「假设字面量 + 删除法」定位**最小冲突规则集**，并给出可执行的调整建议（详见第 5 节）
- **what-if 方案对比** —— 「如果张伟下周只能上 2 天班会怎样」→ 解析成结构化变更 → 重解 → **最小扰动重排**，只改动必须改的几条，其余原封不动（见第 5.2 节）
- **数字护栏** —— 决策说明里的每个数字都必须能在后端算出的事实中找到，拦住大模型的「编数字」
- **人在环确认** —— 低置信度字段以确认卡形式提示用户，而不是直接塞进模型
- **离线可跑** —— 内置 Vue 3，无 CDN 依赖、无构建步骤；`LLM_MOCK=1` 时无需 API Key 即可体验界面

---

## 3. 快速开始

### 3.1 环境要求

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.10+（开发用 3.13.5） | 后端运行环境 |
| Git | 任意 | 版本管理 |
| 网络 | 能访问 DeepSeek API | 仅在非 mock 模式需要 |

> 前端**不需要 Node.js**：Vue 3 已内置在 `backend/app/static/vendor/`，打开页面即用。

### 3.2 安装

```powershell
# 1) 建虚拟环境（注意：把临时目录指到工作区内，避免沙箱/权限问题）
python -m venv .venv

# 2) 安装依赖（国内建议用清华镜像）
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt `
    -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3.3 配置 API Key

```powershell
Copy-Item backend\.env.example backend\.env
# 编辑 backend\.env，把 DEEPSEEK_API_KEY 换成你自己的（https://platform.deepseek.com/api_keys）
```

> `.env` 已在 `.gitignore` 中，**不会**被提交。仓库里只保留 `.env.example`。

### 3.4 启动

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 **<http://127.0.0.1:8000>** 即可使用。API 文档在 <http://127.0.0.1:8000/docs>。

**没有 API Key？** 用离线样例模式：

```powershell
$env:LLM_MOCK="1"
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

**只想验证求解器（完全不联网）？**

```powershell
cd backend
..\.venv\Scripts\python.exe demo_scheduling.py
```

会依次输出「约束合理 → 最优排班」和「约束冲突 → 自动诊断 + 松弛方案」两个完整案例。

### 3.5 队友上手（克隆后必做的四步）

```powershell
git clone https://github.com/bjun3547-jpg/aiweb-decision-agent.git F:\AIWeb
cd F:\AIWeb
```

**① 设成你自己的名字**（不设的话，你的提交会挂在别人名下）

```powershell
git config user.name  "你的名字"
git config user.email "你的邮箱"
git log -1 --format="%an <%ae>"    # 确认生效
```

**② 修复 Git 的 TLS 后端**（Windows 上 git 默认用 schannel，某些机器会直接失败）

```powershell
git config http.sslBackend openssl
```

如果 `git push` 报 `Recv failure: Connection was reset` 或连接超时，说明需要走代理：

```powershell
git config http.proxy http://127.0.0.1:7897    # 换成你自己的代理端口
```

> 这两条是**仓库级配置**，存在 `.git/config` 里，不会跟着仓库同步。每台机器都要自己设一次。

**③ 建 Python 环境**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 用清华镜像，官方源在国内会慢到超时。

**④ 配置并启动**

```powershell
Copy-Item backend\.env.example backend\.env
# 编辑 backend\.env，填入你自己的 DEEPSEEK_API_KEY 或队友共享的 Key

cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000>，点「示例：排得出来」→「开始排班」，能看到排班表就说明环境 OK。

> 没有 Key 也能验证界面：先设 `$env:LLM_MOCK="1"` 再启动，会返回内置样例数据。

### 3.6 三人协作规矩（很重要，不遵守会互相覆盖）

**分工到文件，不要两个人同时改同一个文件：**

| 角色 | 负责的目录 / 文件 | 尽量不要碰 |
| --- | --- | --- |
| 冯璐（队长） | `backend/app/agent.py`、`llm.py`、`prompts.py`、`main.py` | `solver/`、`static/` |
| 李芳青 | `backend/app/static/`（前端页面） | `solver/`、`agent.py` |
| 郑茜文 | `docs/`、`backend/app/solver/` | `static/`、`agent.py` |

**每天的标准动作（照做不会出事）：**

```powershell
cd F:\AIWeb
git pull                                  # ① 动手前先拉别人的改动
# ... 改代码 ...
git add .
git commit -m "feat: 做了什么"
git pull                                  # ② 推送前再拉一次
git push
```

**遇到 `! [rejected] main -> main (fetch first)` 怎么办？**

不要慌，这说明队友比你先推了。执行：

```powershell
git pull --rebase
git push
```

`--rebase` 会把你的提交"叠"在队友的改动之上，不会产生多余的合并记录。

**遇到 `CONFLICT`（冲突）怎么办？**

说明你和队友改了**同一个文件的同一行**。这时候：

1. **不要乱删乱改**，立刻在群里说一声
2. 打开冲突文件，找 `<<<<<<<`、`=======`、`>>>>>>>` 这几个标记
3. 两个人商量保留哪个版本，删掉标记
4. `git add .` → `git commit -m "fix: 解决冲突"` → `git push`

> 根本上避免冲突的办法就是上面的**文件分工**。各改各的文件，冲突概率接近零。

---

## 4. 目录结构

```
AIWeb/
├─ docs/
│  ├─ 00-作战方案.md          # 赛事情报、选题、分工、时间线、提交清单
│  └─ prompts/                # 与 AI 协作的 Prompt 快照（比赛要求）
├─ backend/
│  ├─ app/
│  │  ├─ main.py              # FastAPI 入口 + 静态页面挂载
│  │  ├─ agent.py             # ★ Agent 编排状态机（六步流程 + 数字护栏）
│  │  ├─ llm.py               # LLM 适配层（可换模型、结构化输出、mock 模式）
│  │  ├─ schemas.py           # Pydantic 模型 = LLM 输出的 JSON Schema
│  │  ├─ prompts.py           # 所有 Prompt 集中管理（可版本化、可快照）
│  │  ├─ whatif.py            # ★ what-if：受限操作集补丁 + 方案差异计算
│  │  ├─ solver/
│  │  │  └─ scheduling.py     # ★ CP-SAT 建模 + 冲突诊断 + 自动松弛
│  │  └─ static/
│  │     ├─ index.html        # 前端（Vue 3 单文件，零构建）
│  │     └─ vendor/           # 内置 Vue 3 运行时
│  ├─ demo_scheduling.py      # 离线演示：不联网跑完两个场景
│  ├─ smoke_test.py           # API 冒烟测试
│  ├─ requirements.txt
│  └─ .env.example
└─ README.md
```

---

## 5. 技术亮点：不可行自愈是怎么做的

这是本项目最有辨识度的一段，也是演示视频的高光时刻。

**问题**：用户给的约束经常无解（比如「每班 3 人」+「每人每周最多 5 天」+ 只有 7 个员工）。

**普通做法**：报错「无解」。用户懵，也帮不上忙。

**本项目做法**（三步）：

1. **按规则类别建假设**
   把五类规则（人数需求 / 不可用日期 / 天数上限 / 休息天数 / 连续工作）各建一个布尔「假设」字面量，
   对应约束用 `OnlyEnforceIf(lit)` 挂上去。求解时把它们交给 `AddAssumptions()`。

2. **求解失败 → 调用 `SufficientAssumptionsForInfeasibility()`**
   注意：它返回的是**字面量在模型中的索引**（不是假设列表下标），必须先建反查表。
   而且它给的是「**充分集**」——通常是全部规则，对业务方没有信息量。

3. **删除法收敛到最小冲突集**
   逐条尝试删掉一个规则类别，若「剩下的仍然无解」就真的删掉。
   剩下那 2—3 条，就是真正互相打架的规则。

**最终对用户说的话**（真实输出）：

```
这组约束无法同时满足。求解器定位到一组最小冲突规则
（去掉其中任意一条，剩下的规则都能满足）：

- 各班次人数满足需求（2026-10-19 的「晚班」需要 3 人 等 14 处）
- 个人上班天数不超过上限（张伟 最多上 5 天班 等 7 处）

根因（总量算术）：全部需求合计 42 人班，而 7 名员工在现有规则下
最多只能承担 34 人班，缺口 8 人班。只要需求大于承载，无论怎么排
都排不出来。

可选调整（任选其一）：把每人上班天数上限提到 6 天；
把每班平均需求降到 2.4 人；再增加约 2 名具备相应技能的员工。
```

随后自动把冲突规则转成**带权重的软约束**重解，给出「违约最小」的可行方案，
并逐条列出仍然违反了哪些规则 —— 让用户在「改需求」和「接受近似方案」之间做选择。

### 5.2 最小扰动重排：改一个条件，只动几条班

这是 what-if 最关键的一步，也是一个容易被忽略的工程细节。

**初始版本有个真 bug**：需求约束写的是 `covered >= need`（至少 N 人），而不是「恰好 N 人」，
所以模型完全没有动力去少排人 —— 同一个问题两次求解，一次给出 28 条排班，一次给出 35 条，
**两者在目标函数里同样"最优"**。修法是显式加上总人力成本项。

修完之后又出现第二个问题：**单个人少上一天，整个班表几乎全部重排**（28 条变了 28 条）。
业务上没人接受这种结果 —— 店长要的是「谁顶上这一班」，不是「换一张全新班表」。

**解法**：重解时把上一版方案作为 `baseline` 传进求解器，给每个
「与原方案不同的」（员工, 日期, 班次）组合记一笔惩罚分：

```python
keep = {(a.employee_id, a.date, a.shift_id) for a in baseline}
for key, var in x.items():
    neg = var.Not() if key in keep else var   # 原方案有 → 惩罚"不排"；原方案没有 → 惩罚"排"
    objective_terms.append(neg * COST_STABILITY)
```

目标函数的优先级顺序（数值即权重）：

```
总人力成本(20) > 最小扰动(15) > 公平性(10) > 个人偏好(3)
```

**效果**：让张伟从 4 天减到 2 天，只改动了 **4 条**排班（取消 2、新增 2），
另外 **26 条保持不动**。这正是业务上想要的结果。

---

## 6. 数据护栏：两道拦住大模型幻觉的闸门

| 闸门 | 位置 | 做法 |
| --- | --- | --- |
| **结构闸门** | `llm.chat_json()` | 强制 JSON + Pydantic 校验；校验失败时把错误详情回灌给模型重试一次 |
| **语义闸门** | `solver.repair_problem()` | 抽取结果自相矛盾时确定性修补（例如「班次要求收银技能，却没人具备」→ 临时补齐并**大声提示用户确认**） |
| **数字闸门** | `agent.number_guard()` | 决策说明里的每个数字必须能在「问题参数 + 排班表 + 事实数字」里找到，否则标记为可疑 |

> 这些不是纸上设计。**在接入真实 DeepSeek 时，三个问题都真实出现过**：
> ① 技能要求无人具备 → 语义闸门兜住；② 把 28 个班次写成 35 个 → 数字闸门标记；
> ③ 让模型自己算人均天数 → 改成后端算好再喂给模型。

---

## 7. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 前端页面 |
| GET | `/api/health` | 健康检查 + 模型配置状态 |
| POST | `/api/agent/run` | 自然语言 → 完整决策流程（需 LLM） |
| POST | `/api/agent/whatif` | 「如果……会怎样」→ 变更 + 重解 + 方案对比（需 LLM） |
| POST | `/api/solver/solve` | 结构化参数 → 求解（**不需要** LLM，可离线压测） |
| GET | `/api/meta/rules` | 规则元信息（供前端渲染图例） |

```bash
# 直接调求解器（不经过大模型）
curl -X POST http://127.0.0.1:8000/api/solver/solve \
  -H "Content-Type: application/json" \
  -d '{"problem": {...}, "time_limit": 15}'
```

---

## 8. 验证

```powershell
cd backend

# 离线演示：最优排班 + 冲突自愈
..\.venv\Scripts\python.exe demo_scheduling.py

# API 冒烟测试（mock 模式，不消耗 token）
..\.venv\Scripts\python.exe smoke_test.py

# 真实 LLM 端到端（需要 .env 里配好 Key）
..\.venv\Scripts\python.exe smoke_test.py --real
```

---

## 9. 当前状态与后续计划

**已完成**

- [x] CP-SAT 排班模型（5 类硬约束 + 2 项目标项）
- [x] 自然语言 → 结构化参数（DeepSeek 实测通过）
- [x] 最小冲突集诊断 + 自动松弛
- [x] **what-if 方案对比 + 最小扰动重排**
- [x] 六步 Agent 轨迹可视化
- [x] 数字护栏 + 语义自修补
- [x] Vue 3 单文件前端（零构建、离线可用）
- [x] FastAPI 接口 + 自动 API 文档

**下一步（按优先级）**

> ⏰ 提交截止 2026-11-01。**10/25 之后不再写新功能**，全部时间留给视频和文档。
> 详见 `docs/00-作战方案.md` 第 6 节的 15 天冲刺表。

- [ ] 参数确认交互（把 `uncertainties` 做成可编辑表单后重算）
- [ ] 评测集：30—50 题 benchmark，量化建模正确率与求解耗时
- [ ] 可视化升级：ECharts 甘特图 / 热力图
- [ ] SSE 流式推送，让六步轨迹逐条实时出现
- [ ] 公网演示部署（加分项）
- [ ] 第二个场景（带时间窗的车辆路径 VRP）——**时间不够就砍**
- [ ] 多模态入口：上传需求表截图 → 结构化参数 ——**时间不够就砍**

---

## 10. 说明

- 本项目为传智杯 AI WEB 网页开发挑战赛参赛作品，**开放创新题**，非官方指定赛题。
- 大模型调用使用 DeepSeek 开放平台 API；API Key 仅存于本地 `.env`，不入库。
- 运行 `python demo_scheduling.py` 不需要网络，也不需要 API Key。
