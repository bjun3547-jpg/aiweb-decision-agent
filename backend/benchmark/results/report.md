<!-- 由 backend/benchmark/run_cases.py 生成，模型 deepseek-chat -->
<!-- 生成时间：2026-10-24 22:52，总耗时 195.1 s -->

### 评测集指标（30 题）

| 指标 | 定义 | 结果 |
| --- | --- | --- |
| JSON 一次通过率 | 抽取阶段一次返回合法 JSON / 总题数 | **30 / 30 = 100%** |
| 参数抽取准确率 | 逐字段比对通过数 / 总字段数（30 题 × 10 字段，不含 JSON 失败题） | **99.3%** |
| 建模成功率 | 常规+边界题求出 OPTIMAL / 24 题 | **24 / 24 = 100%** |
| 冲突诊断命中率 | 设计根因被报出 / 6 道冲突题（允许多报） | **6 / 6 = 100%** |
| 冲突诊断精确率 | 除 demand（通用症状）外的规则集合与设计根因完全一致 | **4 / 6 = 67%** |
| 数字幻觉检出 | 数字护栏判定「来源不明」的数字总数 / 触发题数 | **0 个 / 0 题** |
| 端到端平均耗时 | 单题从提交到出结果 | **6.5 s** |

### 逐字段准确率（看模型最容易错在哪）

| 字段 | 准确率 |
| --- | --- |
| rules | 93% |
| employee_count | 100% |
| employee_names | 100% |
| skills | 100% |
| shift_count | 100% |
| shift_hours | 100% |
| shift_skill | 100% |
| dates | 100% |
| demand | 100% |
| unavailable | 100% |

### 逐题结果

| 题号 | 类型 | 抽取 | 状态 | 冲突命中 | 未过字段 |
| --- | --- | --- | --- | --- | --- |
| R01 | regular | 10/10 | OPTIMAL | — | — |
| R02 | regular | 10/10 | OPTIMAL | — | — |
| R03 | regular | 10/10 | OPTIMAL | — | — |
| R04 | regular | 10/10 | OPTIMAL | — | — |
| R05 | regular | 10/10 | OPTIMAL | — | — |
| R06 | regular | 10/10 | OPTIMAL | — | — |
| R07 | regular | 10/10 | OPTIMAL | — | — |
| R08 | regular | 10/10 | OPTIMAL | — | — |
| R09 | regular | 10/10 | OPTIMAL | — | — |
| R10 | regular | 10/10 | OPTIMAL | — | — |
| R11 | regular | 10/10 | OPTIMAL | — | — |
| R12 | regular | 10/10 | OPTIMAL | — | — |
| R13 | regular | 10/10 | OPTIMAL | — | — |
| R14 | regular | 9/10 | OPTIMAL | — | rules |
| R15 | regular | 10/10 | OPTIMAL | — | — |
| R16 | regular | 10/10 | OPTIMAL | — | — |
| R17 | regular | 10/10 | OPTIMAL | — | — |
| R18 | regular | 10/10 | OPTIMAL | — | — |
| B01 | boundary | 10/10 | OPTIMAL | — | — |
| B02 | boundary | 10/10 | OPTIMAL | — | — |
| B03 | boundary | 10/10 | OPTIMAL | — | — |
| B04 | boundary | 10/10 | OPTIMAL | — | — |
| B05 | boundary | 10/10 | OPTIMAL | — | — |
| B06 | boundary | 10/10 | OPTIMAL | — | — |
| C01 | conflict | 10/10 | INFEASIBLE | True | — |
| C02 | conflict | 10/10 | INFEASIBLE | True | — |
| C03 | conflict | 10/10 | INFEASIBLE | True | — |
| C04 | conflict | 10/10 | INFEASIBLE | True | — |
| C05 | conflict | 9/10 | INFEASIBLE | True | rules |
| C06 | conflict | 10/10 | INFEASIBLE | True | — |
