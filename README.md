# 低换型装配线排程 API (Low-Changeover Assembly Line Scheduler)

纯后端服务。给定工单及其配方族（family）、`before → after` 普通前置关系，以及可选的
`immediate` 紧邻工单对，输出一个满足全部约束的完整顺序，优化目标为：

1. **最少换型**：相邻工单 family 不同的次数最少（第 1 个工单不计换型）；
2. 并列时，按工单 id 的 **UTF-8 字节序**取字典序最小的顺序；
3. 列出每次换型的 1-based 位置及前后工单。

## 同族连续工单上限（max_consecutive_same_family）

装配线连续处理同一配方族过久会触发工艺限制。请求可**可选地**给出
`max_consecutive_same_family`（正整数，且不超过本次工单数量；省略或 `null` 表示
不启用）：任何完整顺序中，同族工单连续出现的段长度都不得超过该上限。

- 类型或范围非法（非整数、`0`/负数、超过 18、超过本次工单数；布尔值、浮点、
  数字字符串都不会被隐式接受）一律 **422**，整份请求拒绝。
- 链块内部固定次序本身已超限（某条 `immediate` 链内含超长同族段），或只有在两条
  链拼接后才超限，都在**同一个子集 DP** 中与普通前置、紧邻链一起裁决——不存在
  “先生成旧最优顺序、再检查/修补”的步骤；DP 状态为
  `dp(mask, f, r)`，其中 `f` 是链尾配方族、`r` 是当前同族连续长度。
- 约束彼此一致但不存在满足上限的完整顺序时返回 **UNSCHEDULABLE**，不返回部分
  顺序；普通前置图有环时仍保持原 **CYCLE** 语义（环判定优先，证据不变）。
- 启用上限时，成功响应额外回显 `max_consecutive_same_family`，并给出 `family_runs`
  （顺序中的每个极大同族连续段：family、1-based `start`/`end`、`length`、`jobs`）
  以供复核；**未启用时旧输出逐项不变**。

## 紧邻工单对（immediate）

`immediate` 中每个 `{before, after}` 表示 **after 必须紧跟 before 执行，中间不允许插入
任何工单**。普通前置边只要求先后顺序，无法表达上料后立即加工的工艺要求。

- 每个工单至多有一个紧前项和一个紧后项，因此合法的紧邻关系可串成若干条互不相交的
  **连续链**；非法引用、重复对、同一工单出现两个紧前或两个紧后（分叉）一律 **422**，
  整份请求拒绝。
- 求解时先把每条链收缩为不可拆分的块，再把普通前置边映射为块间约束，链内固定次序与
  链间依赖**在同一个 DP 中一起裁决**；不存在“先求旧最优、再移动工单补救”的步骤。
- 前置图无环，但紧邻要求与前置关系不可同时满足（例如 `a→b→c` 的边与紧邻对 `a→c`、
  块间成环、`a→a` 或仅由紧邻对构成的环）时返回 **UNSCHEDULABLE**，不返回部分排程。

## 算法

**子集状态动态规划**（精确解，非贪心、非启发式）：

```
dp(mask, f) = 已放置 mask 中的链块、最后一个工单 family 为 f 时，
              完成剩余链块所需的最少换型次数
```

块的内部换型在放置块时一次性计入；块与前一工单 family 不同时再计一次边界换型。
n ≤ 18，状态数至多 2^18 × 18，以扁平 `bytearray` 存储（最大代价 17，`INF=127`）；
链块按首工单 id 字节序编号，随后在最优值上按该次序贪心还原，即得唯一的字典序最小
最优工单序列。

启用同族上限时，状态必须扩展为

```
dp(mask, f, r) = 已放置 mask 中的链块、链尾 family 为 f、
                 当前同族连续段长度为 r 时，完成剩余链块所需的最少换型次数
```

同族块只有在拼接后段长仍不超过上限时才允许放置（uniform 整块延长当前段；混合块
在段长加其首段长度合法时落到固定的尾态），链内超长在构造块时直接不可行、跨链拼接
超长在该 DP 的状态转移中不可行——因此“链内已超限”和“仅拼接后超限”在同一次求解
中被区分处理，不会先产出旧最优再返工。`f` 只保留可达到的 `r`（上界为
`min(cap, 该族工单数)`）槽位，扁平 `bytearray` 存储。

## 运行

```bash
docker compose up --build
# API: http://localhost:8000  文档: http://localhost:8000/docs
```

本地（Python 3.12）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

测试：

```bash
pip install -e ".[dev]"
pytest
```

## 请求

`POST /schedule`

```json
{
  "jobs": [
    {"id": "a", "family": "X"},
    {"id": "b", "family": "X"},
    {"id": "c", "family": "Y"}
  ],
  "edges": [
    {"before": "a", "after": "b"}
  ],
  "immediate": [
    {"before": "a", "after": "b"}
  ],
  "max_consecutive_same_family": 3
}
```

约束（违反任一返回 **422**）：

- 2～18 个工单；`id` 唯一，`id`/`family` 均为非空 ASCII 字符串；
- `edges` 可省略（视为空），至多 100 条；
- 自环、重复边、引用未知工单 id、任何层级的额外字段一律 422；
- `immediate` 可省略（视为空）；引用未知工单 id、重复对、同一工单有多于一个紧前项
  或紧后项（分叉）一律 422。`immediate` 中的自环 `a → a` 不是请求格式错误，但任何
  完整顺序都无法满足，故返回 `UNSCHEDULABLE`；同一对同时出现在 `edges` 与
  `immediate` 中是合法的（二者表达不同强度的要求）。
- `max_consecutive_same_family` 可省略或为 `null`；给出时必须是 **JSON 整数**
  （布尔/浮点/字符串均拒绝）且 `1 ≤ 值 ≤ 本次工单数`。

## 响应

成功（200）：

```json
{
  "status": "OK",
  "order": ["a", "b", "c"],
  "changeover_count": 1,
  "changeover_positions": [3],
  "changeovers": [
    {
      "position": 3,
      "from": {"id": "b", "family": "X"},
      "to":   {"id": "c", "family": "Y"}
    }
  ]
}
```

存在依赖环（200，`status=CYCLE`；**不返回任何部分排程**）。`cycle` 是一条
真实有向环，从该环中 id 字节序最小的工单开始，相邻元素（含末→首）的每条边
都存在于输入中；自环表示为 `[id]`。该判定只看普通前置图，即使同时给出了合法的
`immediate` 字段也保持不变。

```json
{
  "status": "CYCLE",
  "cycle": ["a", "b", "c"]
}
```

普通前置图无环，但紧邻要求与前置关系不可同时满足，或启用同族上限后不存在满足全部
约束的完整顺序（200，`status=UNSCHEDULABLE`；**不返回任何部分排程**）：

```json
{
  "status": "UNSCHEDULABLE"
}
```

启用 `max_consecutive_same_family` 时，成功响应在原有字段之外增加：

```json
{
  "status": "OK",
  "order": ["a", "b", "d", "c"],
  "changeover_count": 2,
  "changeover_positions": [3, 4],
  "changeovers": [ "...", "..." ],
  "max_consecutive_same_family": 2,
  "family_runs": [
    {"family": "X", "start": 1, "end": 2, "length": 2, "jobs": ["a", "b"]},
    {"family": "Y", "start": 3, "end": 3, "length": 1, "jobs": ["d"]},
    {"family": "X", "start": 4, "end": 4, "length": 1, "jobs": ["c"]}
  ]
}
```

未启用（省略或 `null`）时，响应与旧版逐字段一致（不含上述两个字段）。

## 测试策略

- `tests/test_oracle.py`：对 n≤7 的随机小图穷举所有排列，直接按题目定义
  （最少换型 + 字节序）求 oracle，逐字段对拍顺序、换型次数与位置；
  含 18 工单性能用例（<15s，实测 <1s）。
- `tests/test_immediate.py`：对 n≤7 的随机图与随机紧邻链穷举所有排列，对拍
  可行性判定（OK / UNSCHEDULABLE）、顺序、换型数与平局；固定用例覆盖链间依赖、
  看似无环却无法紧邻、块间成环、链内反向边、紧邻自环、CYCLE 优先级与
  “不能先求旧最优再移动”的联合裁决场景；含 18 工单带链性能用例。
- `tests/test_cycles.py`：验证环证据每条边均存在、起点为环内最小 id、
  自环、多个环时取最小环、字节序旋转。
- `tests/test_api.py`：422 校验（自环/重复/未知引用/额外字段/数量与类型，
  以及 immediate 的未知引用/重复/紧前紧后分叉/形状）、CYCLE 与 UNSCHEDULABLE
  均不含部分排程、省略 `immediate` 与旧请求逐项一致、成功载荷结构；
  `max_consecutive_same_family` 的类型/范围 422（含 bool/float/字符串、0、超过
  本次工单数）、`null` 与省略逐项一致、启用时的 `family_runs` 结构。
- `tests/test_consecutive_cap.py`：对 n≤7 的随机小图（含较长紧邻链、2 族/3 族）
  穷举所有排列，按定义加上“同族连续段不超过上限”后对拍可行性（OK /
  UNSCHEDULABLE）、顺序、换型最优值与平局，并逐字段复核 `family_runs`；固定用例
  覆盖跨链拼接超限、链内段超限（uniform 链与混合链）、cap=1 强制交替、普通前置
  链迫使超限、块间前置与上限并存、CYCLE 优先级保持，以及 18 工单带上限性能用例。
