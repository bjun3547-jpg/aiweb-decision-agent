# solver/scheduling.py（求解器）与基准测试 —— 模块说明（郑茜文）

> **使用说明**：本文由冯璐按代码实际结构起草骨架，事实部分（文件、行号、常量名）已核对过源码。
> **郑茜文必须做的三件事**：
> 1. 逐条对照 `backend/app/solver/scheduling.py`（758 行）验证下面的说法；
> 2. 把 §三 的每个决策换成你自己的话重写；
> 3. **重写 §四「我还没搞懂的地方」**——必须是你真实的困惑。
> G1 验收时老师会挑一段让你现场讲。

---

## 一、这个文件解决什么问题

一句话：**它是全项目唯一真正"算"的地方**。

整个项目里，大模型只做翻译（自然语言 ↔ JSON），**所有决策都由这个文件里的 CP-SAT 求解器算出**。
这也是「神经符号」这个说法的落点：神经（LLM）负责理解，符号（约束求解）负责计算，两边职责不重叠。

文件 758 行，可以切成四段：

| 行号区间 | 内容 | 干什么 |
|---|---|---|
| 32–76 | 常量区 | 可松弛规则、罚分权重、目标权重、冲突建议文案 |
| 81–228 | 工具函数 | 滑动窗口、容量估算、自动修补、合法性校验 |
| 235–437 | 建模 `_build()` | 把 `SchedulingProblem` 翻译成 CP-SAT 的变量和约束 |
| 440–728 | 求解 + 诊断 `solve()` | 求解、判不可行、找最小冲突、生成建议 |
| 730–758 | 渲染 `render_schedule_table()` | 输出前端能直接用的 markdown 表格 |

---

## 二、数据从哪进来、到哪出去

```
SchedulingProblem（Pydantic 对象，来自 agent.py 的 S2 阶段）
  ├── dates: ["2026-10-26", ...]          计划期每一天
  ├── employees: [{id, name, skills, max_shifts, unavailable_dates, preferred_off_dates}]
  ├── shifts: [{id, name, required_skill, start_hour, end_hour}]
  ├── demand: [{date, shift_id, headcount}]   必须是 dates × shifts 的笛卡尔积
  └── rules: {min_rest_days_per_week, max_consecutive_work_days, ...}
        │
        │  validate_problem()  →  warnings（不阻断，只提示）
        │  repair_problem()    →  自动修补（比如技能无人具备时补技能）
        │  _build()            →  cp_model 的变量 + 约束
        ▼
    cp_model.CpSolver().Solve(model)
        │
        ├── OPTIMAL / FEASIBLE → _extract() → List[Assignment] → render_schedule_table()
        └── INFEASIBLE        → _minimal_conflict_rules() → _diagnose_conflicts() → List[Conflict]
                                     │
                                     └── 带着 baseline 再解一次（what-if）→ 最小扰动重排
```

**输入输出的形状差异要注意**：进来的是「需求」（每天每班要几个人），出去的是「分配」（谁哪天上哪个班）。
前者是 14 条需求，后者是 28 条分配——**这个数量差本身就是求解器的产出**。

---

## 三、最关键的 3 个设计决策

### 决策一：把约束分成「物理约束」和「可谈判约束」两类

代码第 46 行：

```python
RELAXABLE_RULES = ("demand", "availability", "max_shifts", "rest_days", "max_consecutive")
```

**没在这个元组里的**（技能匹配 `skill_match`、一天最多一班）是**永不放宽**的——它们不是"业务偏好"，是物理事实：一个人不可能同时上早班和晚班，不会收银就是不会收银。

**在这个元组里的**五条，都配了一个布尔"闸门"变量（`gate(rule)`），
建模时用 `OnlyEnforceIf(gate(rule))` 挂上去（第 322、335、352、371、387 行）。
闸门打开 = 这条规则生效；闸门关掉 = 这条规则临时不生效，改用松弛变量 `under` / `over` / `short` 兜住缺口，并按 `PENALTY` 罚分。

**为什么这么设计**：现实里"排不出来"的时候，业务方不会说"约束不可满足"，他会问"那我放宽哪一条最划算"。
有了这套闸门，程序才能回答"如果必须排，最少要牺牲什么"——而不是甩一句 INFEASIBLE 就完事。

罚分权重的顺序也说明了态度（第 50–56 行）：

```python
PENALTY = {"demand": 1000, "availability": 500, "max_shifts": 50, "rest_days": 40, "max_consecutive": 40}
```

**宁可让员工多上一天班，也不能让班次没人上**——这是零售业的真实优先级。

### 决策二：冲突诊断用「删除法」找最小冲突集，而不是直接用求解器给的答案

OR-Tools 有现成 API：`SufficientAssumptionsForInfeasibility()`。
但它返回的是**充分集**——往往把全部规则都列进去，等于什么都没说。业务方看到"14 条规则全部冲突"只会更懵。

所以第 489 行 `_minimal_conflict_rules()` 做了一次 delta-debugging（删除法）：

```
for 每条规则:
    试着把它删掉，重新解一次
    如果删掉之后还是无解 → 说明它不是罪魁祸首 → 真的删掉
    如果删掉之后有解了   → 说明它是必需的 → 留着
剩下的那几条，才是真正互相打架的
```

实测效果：12 人以下规模，冲突规则从"全部 14 条"收敛到 2 条，
比如 `['各班次人数满足需求', '个人上班天数不超过上限']`——**这一句话业务方立刻能懂**。

还有一个坑写在第 508 行的注释里：`SufficientAssumptionsForInfeasibility()` 返回的是**字面量在模型中的索引**（`BoolVar.Index()`），
不是假设列表的下标，必须自己建反查表，否则翻译出来的冲突是错的。

### 决策三：目标函数是加权求和，权重的大小关系就是优先级

第 60–63 行：

```python
COST_TOTAL = 20       # 总人力成本
COST_STABILITY = 15   # 最小扰动（只在 what-if 重排时生效）
COST_FAIRNESS = 10    # 公平性（上班天数别差太多）
COST_PREFERENCE = 3   # 个人偏好（想休哪天）
```

`20 > 15 > 10 > 3`，这不是随便排的：

- **成本最高**——店长第一关心的是别多排人；
- **稳定性第二**——改一次方案，如果全班人马大挪移，店员会骂人；所以 what-if 重排要尽量少动；
- **公平第三**——不能老让同一个人上晚班；
- **个人偏好最低**——"李娜周六想休息"是加分项，人手不够时会被牺牲。

注意 `COST_STABILITY` 只在给了 `baseline` 时才有意义（第一次求解没有"原方案"可比）。
这正是 what-if 的实现方式：**把上一次的解当作软约束喂回去**，让求解器"尽量靠近原方案"。

---

## 四、我还没搞懂的地方

> ⚠️ **这一节必须由郑茜文本人重写**。下面三个是我猜你可能会卡住的地方：

1. `OnlyEnforceIf(gate)` 到底是怎么工作的？闸门变量是 `BoolVar`，为什么它能让一整条约束"可选"？
   （提示：CP-SAT 里的 reified constraint，闸门为 True 时约束生效，为 False 时约束自动满足）

2. 删除法的时间复杂度是 O(规则数 × 求解时间)，12 人规模下要解十几次，为什么还这么快？
   （提示：不可行实例的证明通常比可行实例的搜索更快，因为剪枝早）

3. 冗余约束为什么能加速两个数量级？CP-SAT 的"约束传播"到底在做什么？
   （提示：约束越紧，搜索树的剪枝越早；冗余约束不改变最优解，但改变了搜索过程）

---

## 五、基准测试（已完成，10/19）

脚本：`backend/benchmark/run_bench.py`，产物在 `backend/benchmark/results/`。

```powershell
cd F:\AIWeb\backend
..\.venv\Scripts\python.exe benchmark\run_bench.py                 # 3 档 × 5 次
..\.venv\Scripts\python.exe benchmark\run_bench.py --with-llm      # 额外测端到端
```

实测结果（每档同一实例重复 5 次）：

| 规模 | 决策变量 | 需求班次 | 状态 | 中位数 | P90 | 最大 | 目标值 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 人（每班 1 人） | 70 | 14 | OPTIMAL | **78 ms** | 85 ms | 91 ms | 290 |
| 7 人（每班 2 人） | 98 | 28 | OPTIMAL | **13 ms** | 16 ms | 17 ms | 560 |
| 12 人（每班 3 人） | 168 | 42 | OPTIMAL | **149 ms** | 157 ms | 159 ms | 850 |

端到端（7 人档，3 次中位数）：总 6625 ms，其中求解 29 ms，**占 0.44%**。

> **郑茜文要能解释的一个反直觉现象**：5 人档（78 ms）比 7 人档（13 ms）慢。
> 原因不是人多，是**需求宽松**——每班只要 1 人时可行解数量级大得多，
> 求解器要花更多时间**证明**当前解已最优。答辩时被问到这一点，
> 答"影响求解时间的是需求紧度不是人数"，比答"机器卡了"强得多。

### 顺带修掉的一个性能问题（建议主动讲）

原来 5 人档跑 15 秒还只给出 `FEASIBLE`，证不出最优。定位到是建模层缺信息：
需求写的是 `covered >= need`（至少 N 人），"总工作量恰好等于需求"只靠目标函数隐式表达，
求解器没有可剪枝的等式。

补了一条**冗余约束**（不改变可行解集合，只加强传播）：

```python
# 每人每天最多一个班 + 需求必须满足  ⇒  总上班天数 >= 总需求
model.Add(sum(b.work.values()) >= demand_total).OnlyEnforceIf(gate("demand"))
```

效果：**15 s（未证最优）→ 0.08 s（OPTIMAL）**，约两个数量级；12 人档从超时变成 149 ms。

必须挂 `gate("demand")` 的原因：冲突诊断会「删掉 demand 规则」再试解，
那时下界不成立，不挂闸门会把可满足误判成不可行。改完跑冒烟测试，
最小冲突规则集、what-if 差异条数都和改前一致，确认没有回归。

位置：`backend/app/solver/scheduling.py`，`_build()` 里「冗余约束：总工作量下界」那一段。

