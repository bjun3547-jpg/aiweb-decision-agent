# benchmark —— 评测集与压测

第 7 章 §7.1/§7.2 的全部数据来自这里。

## 文件

| 文件 | 作用 |
| --- | --- |
| `cases.json` | 30 题评测集：18 常规 + 6 边界 + 6 冲突。每题 = 一段自然语言 + 一份完整的问题规格（标准答案） |
| `run_cases.py` | 评测集跑分器，三种模式见下 |
| `run_bench.py` | 三档规模的求解性能压测（5/7/20 人，各 5 次），产出 `results/bench.md` |
| `results/` | 实测产物。`report.*` 是 30 题评测结果，`bench.*` 是性能压测结果 |

## 怎么跑

在 `backend/` 目录下执行（虚拟环境在**仓库根目录**，不在 `backend/`）：

```powershell
cd backend

# 1) 离线自证：把标准答案直接喂给求解器，不调模型、不花钱
..\.venv\Scripts\python.exe benchmark\run_cases.py --validate

# 2) 真跑 30 题：调大模型，产出 results/report.csv + report.md
..\.venv\Scripts\python.exe benchmark\run_cases.py --run

# 3) 只重出汇总：改指标口径时用，不重跑模型
..\.venv\Scripts\python.exe benchmark\run_cases.py --report
```

**顺序不能反**：先 `--validate` 跑通 30/30，再 `--run`。
否则跑分时分不清是模型答错还是标准答案写错。

## 两个已删除的脚本

`gen_cases.py`（生成题库）和 `run_eval.py`（离线评测）在 10/25 删除。
它们和现在的 `cases.json` 结构不兼容，原因：

- `gen_cases.py` 会**无条件覆盖** `cases.json`，且日期写死 `2026-10-19 ~ 25`，隔天即失效
- `run_eval.py` 只调 `solve()`、不经过大模型。求解器是确定性的，
  「建模成功率 100%」是必然结果，反映不出模型稳定性

**离线自证这个思路保留了下来**——就是上面的 `--validate`，出处是 `run_eval.py`。

需要看原文：`git show 5d1c596:backend/benchmark/run_eval.py`（或 `gen_cases.py`）。

## 为什么题库用相对日期

题面里一律写「下周」，绝对日期由 `agent._date_anchors()` 在运行时解析。
写死绝对日期的题库会隔天失效——这正是上面 `gen_cases.py` 被弃用的直接原因。
