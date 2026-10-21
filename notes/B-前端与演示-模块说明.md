# index.html（前端）与演示视频 —— 模块说明

> 李芳青 · 消化期产出 · 2026-10-20 起，2026-10-21 随前端重写更新 · 978 行 · 对应技术文档第 5 章前端部分

---

## 一、这个文件解决什么问题

一句话：**把后端的 JSON 变成人一眼能看懂的排班表**。

它是整个项目唯一的用户界面，单文件 `backend/app/static/index.html`（978 行），
用 Vue 3 的浏览器全局构建（`vendor/vue.global.prod.js`）直接挂在 `<script>` 里跑。

它自己不产生任何业务逻辑，不判断约束、不算排班，只做三件事：

1. 收集用户输入的自然语言需求，POST 给 `/api/agent/run`；
2. 把返回的 `AgentRun`（含 `problem` / `result` / `steps` / `narrative`）渲染成三块：**六步执行轨迹**、**排班矩阵**、**决策说明**；
3. 提供一个 what-if 输入框，POST 给 `/api/agent/whatif`，把「改之前 / 改之后」两张表叠在一起做颜色对比。

---

## 二、数据从哪进来、到哪出去

```
用户输入一句话（textarea，绑定 text）
        │
        │  submit()  ← 第 439 行
        ▼
POST /api/agent/run   {text}
        │
        │  返回 AgentRun 的 JSON
        ▼
run.value = data                    ← 第 448 行附近，整个响应塞进一个 ref
        │
        ├── run.steps[]      → 右上/左侧「六步执行轨迹」列表，icon() 决定 ✅/⚠️/❌
        ├── run.problem      → 表头（员工名、日期、班次名）
        ├── run.result.assignments[] → 每个格子是哪个人哪天哪个班
        ├── run.narrative    → 「决策说明」，renderNarrative() 做轻量 markdown 处理
        └── run.problem      → 又被 matrix（computed，第 459 行）转成二维数组给表格用
```

**关键点：`matrix` 是 computed，不是手动拼的。**

后端返回的 `assignments` 是一个**扁平的列表**：

```json
[{"employee_id":"E01","date":"2026-10-26","shift_id":"morning","shift_name":"早班"}, ...]
```

而表格需要的是「第 i 行第 j 列」的**二维结构**。所以 `matrix` 做了一次 reshape：
先用 `employee_id + "|" + date` 建哈希表，再按 `problem.dates` 和 `problem.employees` 的顺序遍历取值
（第 459–481 行）。这样表格的渲染就是纯 `v-for`，不需要任何 DOM 操作。

what-if 是同一套思路的加强版（`wiMatrix`，第 483 行）：同时建 `base` 和 `aft` 两张哈希表，
每个格子返回 `{before, after, changed}`，`changed` 由「改之前的 shift_id」和「改之后的 shift_id」比较得出。
模板里根据 `changed` 上色：变了的高亮，没变的灰掉——**用户一眼能看出这次改动扰动有多大**。

---

## 三、最关键的 3 个设计决策

### 决策一：单文件 + 全局构建，坚决不引入打包工具
在这个项目里，没有`package.json`、vite以及`node_modules`这些文件，`vendor/vue.global.prod.js`是直接提交到代码仓库中的。

我们选择这种方案，是为了让使用者拿到仓库之后，可以直接运行项目。如果引入打包工具，很容易遇到`npm install`安装失败、Node版本不匹配、构建产物忘记提交这几类问题。当前前端只需要打开`index.html`就能查看效果，由FastAPI的`StaticFiles`直接托管，不需要额外构建、也没有额外依赖。

当然这样做也存在代价：整个页面一共978行代码，样式、模板、逻辑全部写在同一个文件里，后续修改查找内容会麻烦一些。但我们认可这个取舍，它保证了项目交付的稳定性。

### 决策二：前端不做任何业务判断，只做「形状转换」
前端不会去校验诸如“某人当天是否请假”、“当班人员数量是否充足”这类业务规则，全部的业务判断逻辑都交给后端处理。前端的工作仅仅是把`assignments`扁平化列表，转换重塑为二维矩阵表格。

我们这样划分前后端边界的原因：如果前端和后端同时处理业务规则，很容易出现前后判断结果不一致的情况，修改代码时容易出现遗漏。按照现在的边界约定，后端返回什么数据，前端就渲染展示什么内容，职责划分清晰。

### 决策三：what-if 使用「颜色编码 diff」，而不是并排展示两张表
并排摆放两张表格看似直观，但人工逐行对比7×7的矩阵十分费力。所以我们采用`wiMatrix`，将新旧两组排班结果叠加在同一张表格内，依靠`changed`字段控制单元格样式，发生改动的单元格高亮，没有改动的单元格保持暗色。

这样带来的效果：文字上显示`取消 2 条 / 新增 2 条 / 未变 26 条`，在表格上直接体现为两个高亮格子，不需要仔细阅读文字就能快速看懂差异。这也把算法「最小扰动」的目标可视化了：算法尽量减少排班改动，界面就把改动的地方突出展示。


---


## 四、我还没搞懂的地方

1. Vue中的`computed`计算属性和普通函数之间有什么区别？如果我现在把`matrix`相关逻辑改成普通`function matrix()`来实现，页面会产生什么样的变化？
我了解到`computed`自带缓存机制，依赖数据不变时不会重复执行计算，在模板中可以直接当作属性调用，不需要写括号执行，但我还没有实际体会到二者在项目运行时性能上的差别。

2. 在what-if对比功能中，存在base基准排班表与aft修改后的排班表，为什么`baseline_result`必须交给后端重新运算，不能直接复用上次`run.result`返回的结果？
我初步理解如果用户连续多次执行what-if操作，第二次对比的基准应该是上一轮修改后的排班，而不是最原始的初始方案，但对数据流转完整链路还不够清楚。

3. 在录制项目演示视频时，如何稳定捕捉六步执行轨迹中的求解耗时（如40ms）？
我知道可以提前多次试跑，挑选展示效果好的一次进行录屏，也可以把快速求解当作项目亮点进行答辩讲解，但还不确定在录屏时如何稳定捕获这个耗时数字，避免出现数据一闪而过录不到的情况。


---

## 五、演示视频的对应关系

录视频时，下面的镜头和本文档的章节是对应的，讲的时候别跑偏：

| 镜头 | 画面 | 对应章节 |
|---|---|---|
| 1 | 首页，粘贴示例需求 | §一 |
| 2 | 点「开始排班」，六步轨迹逐条亮起 | §二 的数据流 |
| 3 | 排班表出现，早班/晚班不同颜色 | §三 决策二 |
| 4 | **特写「精确求解 ~30 ms」** | §三 决策一（这是全片最值钱的 3 秒） |
| 5 | 决策说明文字 | §二 的 narrative 分支 |
| 6 | what-if 输入「张伟下周只能上 2 天」，表格变色的两个格子 | §三 决策三 |
| 7 | 收起，团队信息 | — |

---

# 六、5 个验收问题（答题骨架）

> ✍️ **李芳青：下面每一条的「事实」部分是冯璐对着 `index.html` 核对过的，可以直接用；
> 「你要写的」部分必须你自己补，而且**要用你自己的话**。G1 会挑一段让你现场讲。**
>
> 行号对应 `backend/app/static/index.html`，**978 行**（2026-10-21 18:00 的手机端修复之后）。

## 0. 行号会过期，用这三条命令自己刷

这个文件三个人在改，行号每改一次 CSS 就会整体平移。**别背行号，背锚点**，需要时现查：

```bash
# 1) 文件现在多少行？不是 978 就说明又改过了
wc -l backend/app/static/index.html

# 2) 六个区块的标题各在第几行（左边是行号，右边是锚点文本）
grep -n '<h2>' backend/app/static/index.html

# 3) 关键函数/变量在第几行
grep -n 'const matrix = computed\|async function submit\|function parseErr\|async function postJSON\|function icon\|const balance = computed\|const wiMatrix = computed' backend/app/static/index.html
```

> 下面写的「约 XXX 行」是**锚点行号**，配合上面第 2、3 条命令用。
> 上次改动：10/21 18:00 加了手机端 CSS（`textarea` 加高、员工列钉住、图例不断行），
> 一共 +13 行，所以 978 之前的旧行号**全部要加 13**。

---

## Q1　页面一共几个区块？每个区块的数据分别来自后端返回的哪个字段？

**事实**：左右两栏，左栏 6 块（`<section>`，316–571）、右栏 6 块（`<aside class="side">`，574–652）。

| # | 区块 | 数据来源 | 行号 |
| --- | --- | --- | --- |
| ① | 用中文描述你的排班需求 | 本地 `text`（ref，665）；两个示例文案在 `samples`（683–692） | 317–331（标题 318） |
| ② | Agent 执行轨迹 | `run.steps[]` 的 `status` / `name` / `detail` / `duration_ms` | 333–378（标题 334） |
| ③ | 决策建议 | `run.narrative` → `renderNarrative()`（958–963） | 380–383 |
| ④ | 冲突诊断 | `run.result.conflicts[]` / `.diagnosis` / `.violations[]` | 385–398 |
| ⑤ | 排班表 | `matrix`（computed，781–816）← `run.problem` + `run.result.assignments` | 骨架屏 400–411 / 真表 413–451 |
| ⑥ | 如果……会怎样（方案对比） | `wi`（`/api/agent/whatif` 的返回） | 453–570 |
| 右1 | 关键指标 | `run.problem.employees` / `.dates` / `run.result.assignments` / `.solve_time_seconds` | 575–593 |
| 右2 | 工时均衡度 | `matrix` + `balance`（computed，825–843） | 596–624 |
| 右3 | 需要你确认 | `run.extraction.uncertainties[]` | 626–632 |
| 右4 | 自动修补与提醒 | `run.warnings[]` | 634–637 |
| 右5 | Agent 的理解 | `run.extraction.summary` | 639–642 |
| 右6 | 工作原理 | 纯静态文案，不来自后端 | 644–652 |

**一个容易漏的细节**：⑤ 在 DOM 里是**两块**——`v-if="loading && !matrix"` 是骨架屏（400–411），`v-if="matrix"` 才是真表（413–451）。所以「⑤ 排班表」这个标题在**加载中也会出现**。这一点在 Q4 会用到。

**你要写的**：左栏是这次任务的**主流程**，从输入一路走到方案对比，必须按顺序看；右栏是**旁注**，放指标、均衡度、待确认项、提醒和静态说明，随时瞄一眼就行。这样分栏是为了让用户看结果时视线不用来回跳。

---

## Q2　`submit()` 里从点击到出结果，完整经过哪几步？出错时错误信息显示在哪？

**事实**（`submit()` 在 759–779；真正的网络请求封装在 `postJSON()`，715–736）：

1. **防重入**：`if (loading.value) return`（760）
2. **清场**：`loading=true`、清空 `error` / `run` / `wi` / `wiText` / `wiError`、收起两个「技术细节」（761–763）
3. **起骨架计时器**：`skStepIdx=0` + `setInterval` 每 900 ms 推进一格（764–768）——等待期间六步是「一个个亮起来」的，不是干等
4. **发请求**：`postJSON("/api/agent/run", { text })`（770）
5. **`postJSON` 内部**（715–736）：`fetch` 带 `AbortController`，默认 90 s 超时（716–717）；`r.json()` 解析失败 → 抛「服务返回的不是 JSON，后端可能没启动或已崩溃」（726–729）；`!r.ok` → 抛 `data.detail` 或 `HTTP <status>`（730）；`AbortError` → 转成字符串 `"timeout"`（733）
6. **成功**：`run.value = data`（771），靠 Vue 响应式自动重渲染
7. **业务层错误**：HTTP 200 但 `data.ok === false` → `error.value = data.error`（772）
8. **异常**：`catch` → `error.value = "请求失败：" + e.message`（774）
9. **收尾**：`finally` 清计时器 + `loading=false`（775–778）

**错误显示在**：② 卡片里的 `<div class="errbox" v-if="error">`（353–367）。不是一行红字，是一张卡片：

- 标题 + 人话建议来自 `errInfo`（756）← `parseErr()`（739–755）把原始报错**分成四类**：`timeout` → 「求解超时」、`Failed to fetch` → 「连不上服务器」、`HTTP 5xx` → 「服务端出错」、`HTTP 4xx` → 「请求被拒绝」
- 两个按钮：`重新排班`、`查看技术细节`
- 原始报错藏在 `v-if="showErrDetail"` 里，默认不吓用户

**你要写的**：第 5 步和第 7 步是两层错误。`postJSON` 里抛的是**通信层**失败（请求压根没拿到正常响应），`data.ok === false` 是**业务层**失败（接口通了，但 Agent 排班没成功）。分开处理是为了给用户不同提示——前者让他去检查后端和网络，后者告诉他需求本身有问题。再加上 `parseErr` 的分类，用户看到的是「连不上服务器」，而不是 `TypeError: Failed to fetch`。

---

## Q3　`schedule_table_md` 是一段 Markdown 文本，前端是怎么把它变成排班表格的？

> ⚠️ **这题的前提是错的，这是一个陷阱。**

**事实**：`grep -c schedule_table_md backend/app/static/index.html` → **0**。前端**根本没有用**这个字段。

⑤ 排班表是这样来的：

```
run.result.assignments[]（扁平列表，28 条，每条形如 {employee_id, date, shift_id, shift_name}）
        │  matrix（computed，781–816）
        │  789–790：先用 employee_id + "|" + date 建哈希表
        │  791–801：再按 problem.employees / problem.dates 的顺序双层遍历取值
        │  803–808：顺便算每人 worked 和热力色
        ▼
{matrix: {dates[], shifts[], rows[{id, name, cells[], worked, heatColor}], maxWorked, minWorked}}
        │  模板里纯 v-for（425–435）
        ▼
⑤ 的 <table class="sched">（416–437）
```

`schedule_table_md` 只给 **CLI / 需要复制文本**的场景用（`agent.py` 的 `_cli()` 会打印它）。

**你要写的**：这题的前提有误，前端并没有使用 `schedule_table_md`。排班表是前端把 `run.result.assignments` 这个扁平列表，用 computed 重组成「员工 × 日期」的二维 `matrix`，再用 `v-for` 渲染成表格。不用 Markdown 是因为这张表要按班次着色、算每人上班天数、算工时热力、还要和 what-if 的新表逐格对比——这些 Markdown 都做不到。

**这题的加分点**：主动指出「题目假设有误」，比硬编一个解释强得多——说明你真的读过代码。

---

## Q4　六步轨迹有 `ok / warn / error / running` 几种状态，界面上分别怎么表现？

**事实**：

- 状态值实际有 **5 种**：`pending` / `running` / `ok` / `warn` / `error`
- 图标映射在 `icon(s)`（955–957）：

```js
{ ok: "✓", warn: "!", error: "×", running: "…", pending: "" }[s]
```

- 颜色由 `<span class="dot" :class="s.status">`（370）驱动，`.dot.ok` / `.warn` / `.error` / `.running` 在 CSS 第 77–78 行各一套；`running` 还有呼吸动画（205）
- 耗时显示：`s.duration_ms ? s.duration_ms + ' ms' : ''`（375）——0 就不显示，所以「数据校验 0 ms」那一行没有数字
- **加载中的骨架屏用的是另一套**：`skView`（707–712）把 `skStepIdx` 映射成 `cls`，所以加载时只会出现 `ok` / `running` / `pending`，不会出现 `warn` / `error`（337–350）

**你要写的**：`warn` = 这步没完全成功，但方案还能用；`error` = 这步挂了，后面走不下去。用不同图标是为了让用户一眼看出「是结果有瑕疵，还是根本没结果」。

（提示：后端真正会置 `warn` 的只有两处——`agent.py:349` 是数字护栏发现说明里有来源不明的数字，`agent.py:357` 是 explain 阶段 LLM 调用失败。两种都是「说明写得不好，但排班表是好的」。）

---

## Q5　如果现在要加一个「工时热力图」，你会改哪几行？数据从哪取？

> 💡 **先别急着答「加个 computed」——这个功能其实已经做了一半。**

**事实**：页面上已经有两处「工时可视化」：

1. **排班表最右一列「工时」**（433–434）：每人的上班天数按深浅着色，`heatColor` / `heatText` 是 `matrix` 里算好的（806–807）
2. **右栏「工时均衡度」卡片**（596–624）：每人一条横向条形，长度 = 上班天数

配色函数在 917–952：`heatRatio()`（归一化到 0~1，921）、`getHeatBgColor()`（蓝系插值，930）、`heatTextColor()`（深底换白字，943）、`barColor()`（948，条形专用实色）。

**如果要做的是「按格」的工时热力图**（每格不显示「早班/晚班」，而显示小时数），只动 3 处、**不用改后端**：

1. **`matrix` 里加一个字段**（改 791–801 的 `cells` 映射）：现在返回 `{label, color}`，再加一个 `hours` —— 用 `shifts.findIndex` 找到班次，取 `end_hour - start_hour`
2. **模板加一张卡片**（放在 ⑤ 后面，约 452 行）：`v-for` 套同一个 `matrix.dates` / `matrix.rows`，单元格背景色按 `hours` 深浅
3. **CSS 加四档 class**（`<style>` 里，7–296 之间）：`.heat-0` ~ `.heat-3`

**数据从哪取**：

- 每格「哪个班」→ `run.result.assignments[]`（和 `matrix` 同源）
- 每格「几小时」→ `run.problem.shifts[]` 的 `start_hour` / `end_hour`（早班 8–16 = 8 小时，晚班 16–24 = 8 小时）
- 每人每周总工时 → 把该员工 7 天的 `hours` 加起来

**你要写的**：后端已经返回了全部原始数据——`assignments` 有谁哪天哪个班，`shifts` 有班次的起止小时。工时热力图只是前端换个展示形状重算一遍，不需要新接口、也不需要后端补字段。而且按人的工时热力现在已经在做了（「工时」列 + 「工时均衡度」条形图），要补的只是按格的粒度。

（提示：这正是 §三 决策二说的「前端只做形状转换」。）

---

## 附：手机端已经改过一轮（2026-10-21）

冯璐拿真机 4G 测过一遍，发现三个窄屏问题，已在 `index.html` 里修掉，**答辩时如果被问到「移动端适配做了什么」，答这三条**：

| 问题 | 原因 | 修法 | 行号 |
| --- | --- | --- | --- |
| 示例需求被截成半行，看不到「张伟周三请假」 | `textarea` 的 `min-height:104px` 在 390px 宽下只够 6 行，文案要 8 行 | 窄屏下 `min-height:172px` | 263–265 |
| 表格横滑到右边看「上班/休息/工时」时，姓名列跟着滑走，整屏全是色块 | 没有固定列 | `position:sticky; left:0` 钉住姓名列，再用 `box-shadow:0 0 0 3px` 把 `border-spacing` 的缝糊住 | 270–278 |
| 图例「（全员 4 天 · 完全均衡）」被断成「完全均 / 衡）」 | 中文可以在任意字符处断行 | `.heat-legend b{white-space:nowrap}` | 284–285 |

另外把滑动提示从「← 左右滑动可查看全部日期 →」改成「← 左右滑动可查看「上班 / 休息 / 工时」→」——原来那句话是错的，日期本来就全看得见，藏起来的是统计列（439 行）。
