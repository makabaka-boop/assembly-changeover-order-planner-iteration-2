# 低换型装配线排程 API (Low-Changeover Assembly Line Scheduler)

纯后端服务。给定工单及其配方族（family）、`before → after` 普通前置关系，以及可选的
`immediate` 紧邻工单对，输出一个满足全部约束的完整顺序，优化目标为：

1. **最少换型**：相邻工单 family 不同的次数最少（第 1 个工单不计换型）；
2. 并列时，按工单 id 的 **UTF-8 字节序**取字典序最小的顺序；
3. 列出每次换型的 1-based 位置及前后工单。

## 同族连续上限（`max_same_family_run`）

装配线连续处理同一配方族过久会触发工艺限制。可选地设置 `max_same_family_run`
（1～18 的整数；省略或为 `null` 表示不启用），要求结果中每一段连续同族工单的长度
都不超过该值。求解在**同一个 DP** 内同时裁决普通前置、紧邻链与连续上限——状态为

```
dp(mask, f, r) = 已放置 mask、最后一个工单 family 为 f 且当前同族连续长度为 r 时
                 完成剩余链块所需的最少换型次数
```

因为超限既可能发生在**单条紧邻链内部**（该块永远无法放置），也可能只在**两条同族
链拼接后**才发生，不存在“先求无上限最优、再移动工单补救”的步骤。启用上限但不存在
合法完整排程时返回 **UNSCHEDULABLE**（不返回部分顺序）；普通前置环仍优先按原语义
返回 **CYCLE**。

启用上限时响应额外给出 `family_runs`（结果中各同族连续段的 family、1-based
起止位置、长度与工单 id 列表，供复核）；**未启用时响应逐字段保持不变**。

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

启用同族连续上限时使用状态 `(mask, family, run_length)` 的子集 DP：块首同族则尝试
延续当前连续段（合并长度不得超过上限，单族块的后继 run 长度随当前段增长，多族块
结束于自身尾段），异族则计一次边界换型并把连续段重置为块的尾段长度；同样按链块
编号次序贪心还原。不存在满足上限的完整排程时该 DP 返回无解。

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
  "max_same_family_run": 2
}
```

约束（违反任一返回 **422**）：

- 2～18 个工单；`id` 唯一，`id`/`family` 均为非空 ASCII 字符串；
- `edges` 可省略（视为空），至多 100 条；
- 自环、重复边、引用未知工单 id、任何层级的额外字段一律 422；
- `immediate` 可省略（视为空）；引用未知工单 id、重复对、同一工单有多于一个紧前项
  或紧后项（分叉）一律 422。`immediate` 中的自环 `a → a` 不是请求格式错误，但任何
  完整顺序都无法满足，故返回 `UNSCHEDULABLE`；同一对同时出现在 `edges` 与
  `immediate` 中是合法的（二者表达不同强度的要求）；
- `max_same_family_run` 可省略或为 `null`；给出时必须是 1～18 的整数
  （布尔值、浮点、数字字符串均 422）。合法但不存在满足上限的完整排程时返回
  `UNSCHEDULABLE`。

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

启用 `max_same_family_run` 时额外携带 `family_runs`（未启用时不出现该字段）：

```json
"family_runs": [
  {"family": "X", "start": 1, "end": 2, "length": 2, "jobs": ["a", "b"]},
  {"family": "Y", "start": 3, "end": 3, "length": 1, "jobs": ["c"]}
]
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

普通前置图无环，但紧邻要求与前置关系（或同族连续上限）不可同时满足（200，
`status=UNSCHEDULABLE`；**不返回任何部分排程**）：

```json
{
  "status": "UNSCHEDULABLE"
}
```

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
  以及 immediate 的未知引用/重复/紧前紧后分叉/形状、`max_same_family_run`
  的类型与 1～18 范围）、CYCLE 与 UNSCHEDULABLE 均不含部分排程、省略
  `immediate`/`max_same_family_run` 与旧请求逐项一致、启用上限时 `family_runs`
  结构、成功载荷结构。
- `tests/test_run_cap.py`：对 n≤7 的随机图与随机紧邻链穷举所有排列，对拍可行性
  （OK / UNSCHEDULABLE）、换型最优值与字节序平局，并校验连续段分区与上限；
  固定用例覆盖**跨链拼接超限**（无分隔工单则无解、有则强制多一次换型）、
  **链内超限**、普通前置边强制超限、跨链前置边拼接超限、cap=1、CYCLE 优先级
  与“未启用逐字段不变”；含 18 工单上限性能用例（<15s）。
