# 答辩题库 · agent.py + llm.py（冯璐）

> 用法：**先合上代码自己答一遍**，再对照下面的参考答案。
> 答不出来的那一条，就是还没读懂的地方 —— 把它写进模块说明第四节。
> 这 5 题同时是技术文档第 4 章和视频「Agent 内部机制」段的脚本来源。

---

## Q1　六个步骤分别是什么？每一步的输入和输出各是什么类型？

**六步**（定义在 `agent.py:96` 的 `STEP_NAMES`）：

| # | key | 中文名 | 输入 | 输出 |
| --- | --- | --- | --- | --- |
| S1 | `understand` | 理解需求 | `text: str` | `ExtractionResult`（含 `SchedulingProblem` + `summary` + `uncertainties`） |
| S2 | `validate` | 数据校验 | `SchedulingProblem` | `SchedulingProblem`（可能被修补）+ `list[str]` warnings/repairs |
| S3 | `model` | 数学建模 | `SchedulingProblem` | 无独立类型（建模动作发生在 S4 的 `_build()` 内部） |
| S4 | `solve` | 精确求解 | `SchedulingProblem` | `SolveResult`（`status` / `assignments` / `conflicts` / `violations` / `diagnosis`） |
| S5 | `diagnose` | 冲突诊断 | `SolveResult` | 只做状态映射（`ok` / `warn`），不产生新数据结构 |
| S6 | `explain` | 生成决策说明 | `SchedulingProblem` + `SolveResult` | `str`（中文决策说明）+ `number_guard` 的核查结果 |

**加分回答**（能说出来会明显加分）：

- 六步里**只有 S1 和 S6 调用大模型**，S2/S3/S4/S5 全是确定性代码。这是整个项目"神经符号"的核心结构。
- 每一步都记录 `status`（`pending`/`running`/`ok`/`warn`/`error`）、`duration_ms`、`detail`，前端直接拿这个渲染"六步轨迹"面板。
- S4 用 `asyncio.to_thread(solve, ...)` 包住（`agent.py:262`）——CP-SAT 是同步阻塞的 CPU 计算，不包线程池会把整个事件循环卡死。

---

## Q2　为什么用显式状态机而不是 ReAct 循环？至少两个理由。

**理由一：流程本身是确定的，用 ReAct 是拿不确定的机制去发现确定的流程。**

排班这件事，"识别需求 → 校验 → 建模 → 求解 → 诊断 → 解释"的顺序不随输入变化。ReAct 的价值在于"我不知道下一步该干嘛，让模型自己判断"，而这里每一步该干嘛是**已知的**。让模型重新"推理"一遍已知的流程，唯一的效果是引入随机性：同样的输入可能走 5 步也可能走 7 步。

**理由二：固定步数才能做可视化，而可视化是核心卖点。**

前端那个"六步轨迹 + 每步耗时"的面板，前提是步骤集合固定。这正是"神经符号"最有说服力的展示——评委能直接看到 S1 花 4132ms、S4 只花 13ms，一眼就明白"计算不在大模型里"。ReAct 的步骤数是动态的，这个面板根本画不出来。

**理由三：失败可以精确定位。**

状态机里 S1 挂 = Key/网络问题，S4 挂 = 建模问题，S6 挂 = 不影响方案（`agent.py:321` 只标 `warn` 不中断）。ReAct 循环里出错，你只知道"某一轮推理错了"，排查成本高一个数量级。

**理由四：可复现性。**

同一个输入，状态机每次走的路完全一样，bug 能稳定复现。ReAct 的轨迹不稳定，测试和 debug 都变难。

> ⚠️ 别说成"ReAct 不好"。正确说法是：**ReAct 适合开放式探索任务，我们是封闭式决策任务，用状态机更合适。** 这个区分能体现你真的想过，而不是背结论。

---

## Q3　`number_guard()` 怎么判断一个数字"来源不明"？为什么把 1–10 放进白名单？

**判断机制**（`agent.py:63`）：

```python
allowed = _numbers_in(allowed_text) | {str(i) for i in range(1, 11)}
used    = _numbers_in(narrative)
return sorted(used - allowed, key=lambda x: (len(x), x))
```

1. `_numbers_in()` 用正则 `\d+(?:\.\d+)?` 抽出文本里所有数字，并做前导零清洗（`"08"` → `"8"`），避免 `08` 和 `8` 被当成两个不同数字。
2. `allowed` = **事实材料里出现过的所有数字** ∪ **1–10**。
3. `used` = 决策说明里出现的所有数字。
4. 返回差集，按长度再按字典序排序——这样 `35` 会排在 `3` 前面，报错时先暴露更可能是幻觉的长数字。

**"事实材料"具体是什么**（`agent.py:306`）：
`problem.model_dump_json()` + 排班表格 Markdown + `result.model_dump_json()` + `fact_numbers()` 的 JSON。
也就是说：**只要这个数字没在需求、排班表、求解结果或预计算指标里出现过，就判定为模型自己编的。**

**为什么 1–10 要白名单**：

中文决策说明里天然会大量出现小数字，而且这些数字**不是数据，是修辞**：

- 序数：`第一点`、`第二步`
- 量词：`两条建议`、`三个方面`
- 泛指：`一周`、`两天`

这些数字在事实材料里根本找不到，如果不放行，几乎每一段说明都会被判"有幻觉"，护栏直接失效。

> **代码里没有注释说明为什么上界是 10**，这是根据实际误报情况定的经验阈值。如果有人问"为什么不是 20"，诚实回答："10 是当时定的经验值，没有做过系统的阈值实验，这是我们已知的一个可以改进的点。"——这比硬编一个理由好。

---

## Q4　LLM 返回的 JSON 不符合 schema 时，`chat_json()` 具体做了什么？

**位置**：`llm.py:178`，关键参数 `max_retries: int = 1`。

**完整过程**：

1. 第一次请求带上 `response_format={"type": "json_object"}`，强制模型输出 JSON 对象。
2. 拿到 `content` 后用 `schema_model.model_validate_json(content)` 做 Pydantic 校验。
3. **校验失败时，不是简单重发**，而是往 `messages` 里追加两条：
   - `{"role": "assistant", "content": content}` —— 把模型**自己的错误输出**原样放回去
   - `{"role": "user", "content": "上面的 JSON 未通过结构校验。请严格按约定的 schema 重新输出，只输出 JSON 本身。错误详情：<具体的 exc.errors()>"}` —— 把**具体哪几个字段错了**告诉它
4. 重新请求。所以是**总共最多 2 次**（1 次初始 + 1 次重试）。
5. 第二次还失败，抛 `LLMError`，错误信息里带 `exc.errors()[:5]` 和 `content[:500]`，方便开发者定位。

**为什么要"带着报错重试"而不是"直接重试"**：
模型看到"你上次漏了 `employees[2].max_shifts`，类型应该是 int"，第二次命中率显著提高；盲目重试只是让它重复同一个错误，白花钱。

**为什么只重试 1 次**：
再失败基本说明 prompt 或 schema 本身有问题，继续重试是烧钱拖时间。这时应该把原始输出抛给人看，改 prompt。

**加分点**：`temperature=0.1`（`llm.py:184`）——结构化抽取任务不需要创造性，温度压到最低能明显提高格式稳定性。

---

## Q5　what-if 里为什么要先把原问题也重算一遍 `baseline`？

**位置**：`agent.py:426`

```python
baseline = await asyncio.to_thread(solve, problem, time_limit=15.0)
after    = await asyncio.to_thread(solve, patched, time_limit=15.0, baseline=baseline.assignments)
```

**三个理由，缺一不可：**

**理由一：对比要公平，两次求解必须用同一套配置。**

如果直接拿上一次接口返回的结果当 baseline，那次可能是不同的 `time_limit`、不同的代码版本、甚至不同的 `problem`（用户中途改过需求）。这样算出来的"取消 2 条 / 新增 2 条"可能根本是配置差异，不是假设带来的影响。重算保证两次求解在**同一时刻、同一配置**下进行。

**理由二：`baseline.assignments` 是最小扰动目标的参照系。**

`after` 那次求解传了 `baseline=baseline.assignments`，求解器会把"偏离 baseline 的班次"计入惩罚项。目标函数权重是：

```
COST_TOTAL = 20  >  COST_STABILITY = 15  >  COST_FAIRNESS = 10  >  COST_PREFERENCE = 3
```

所以顺序是：先保证总人力成本最优，再尽量少改动，再谈公平，最后才谈个人偏好。**没有 baseline 就没有"少改动"这个目标**，求解器会随便给一个同样最优但面目全非的方案——那 what-if 就失去了"最小扰动"的意义。

**理由三：CP-SAT 本身有不确定性。**

求解带时间限制，不同次运行可能返回不同的最优解（多个方案同分时）。用历史结果当基线，等于拿两次不同随机性的结果做差，diff 里会混进噪声。

**这就是产品卖点**：改一个条件，只动 4 条班次（取消 2 / 新增 2 / 未变 26）。如果每次重排都大改，店长根本不敢用——人是要记住自己排的班的。

---

## 附：如果被问到"你们的大模型到底做了什么"

一句话：**它只做两件事——把中文翻译成结构化参数（S1），把结构化结果翻译回中文（S6）。中间所有计算、约束、诊断，一行大模型代码都没有。**

数据支撑：端到端 6178 ms，其中大模型占 6160 ms（99.79%），CP-SAT 求解占 13 ms（0.21%）。
