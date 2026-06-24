# 多 Agent 编排 · 设计规格

> ⚠️ **这是设计蓝图,不是已实现代码的说明。** 协作层(M5–M7)尚未实现;本文是你实现时照着走的"规格"。
> 它是 [`05-技术架构设计.md`](./05-技术架构设计.md) §4.3 的**深挖展开**——整体架构讲"多 Agent 在哪一层",本文讲"多 Agent 具体怎么设计"。
> 对标 Claude Code 仓库 `docs/agent-orchestration.md` §3–§4.5。

---

## 0. 一条贯穿全篇的主张

> **多 Agent 编排不是一个新系统,而是「单 Agent 主循环」的三种用法:**
> 1. **递归**地调用它自己(子 Agent)
> 2. 在它之上加**星型**派活与回灌(协调器)
> 3. 在它之间加**网状**直接通信(mailbox)

所以读本文前,必须先理解内核主循环(M4,见 [`07-迭代开发计划.md`](./07-迭代开发计划.md))。下面三章 = 三种用法。

---

## 1. 子 Agent:递归 + 上下文隔离(M5)

### 1.1 核心机制

子 Agent **不是**另写的并发框架,而是:

```
父 Agent 的 loop.py
   └─ 模型决定调用 AgentTool(它本身是一个工具)
        └─ AgentTool.run() 内部:
             ① 造一份隔离上下文(干净 messages + 子任务 prompt,depth+1)
             ② 调 run_loop(...)  ← 就是 M4 那个主循环,递归!
             ③ 只把子 Agent 的【最终结论】return 给父级
```

### 1.2 三个必须做对的设计点

| 设计点 | 怎么做 | 为什么 |
|---|---|---|
| **上下文隔离** | 子 Agent 拿干净的 messages,不继承父级全部历史 | 子 Agent 烧自己的预算干脏活,父级"脑容量"不被污染。对标 runAgent.ts §3.1 |
| **只回传结论** | 子循环跑完,只把最终答案返回,丢弃中间过程 | 父级上下文只增加一句结论,不是几十轮工具记录 |
| **递归守卫** | `ctx.depth` 超上限就拒绝再派 | 防 Agent 套 Agent 无限递归烧钱。对标 isInForkChild |

### 1.3 并行从何而来(关键)

`AgentTool` 标记 `is_concurrency_safe = True`。于是当模型一轮里派出**多个**子 Agent,它们落入 M3 的"只读并发批",**自动并行**——你**不需要为"多 Agent 并行"写任何专门代码**。

```
模型一轮: [AgentTool(任务A), AgentTool(任务B), AgentTool(任务C)]
          → 全是 concurrency_safe → 同一并发批 → gather 同时跑
```

> 这是整个设计最优雅的一点:**并行是"工具声明 + 分批规则"的副产品,不是新机制。**

### 1.4 Routing 在这里涌现

"派哪个子 Agent / 给它什么任务"这个选择,就是 **Routing 模式**。无需单独实现——它是 `AgentTool` 调用参数的自然结果。

---

## 2. 协调器:星型拓扑(M6,Orchestrator-Workers)

### 2.1 形态

```
                    leader(只派活 + 综合,不自己下场写)
                  ╱        │        ╲
            worker A    worker B    worker C   (并行,各自是一个子 Agent)
                  ╲        │        ╱
              结果以"伪用户消息"回灌 leader
```

### 2.2 三个设计点

**① 角色分离(靠 system prompt)**
leader 的 system prompt 改成:"你只负责拆任务、派给 worker、综合结果、跟用户沟通;不要自己写代码。"对标 `coordinatorMode.ts` 的 coordinator prompt。

**② worker 工具收窄(靠白名单)**
worker 拿不到"再派子 Agent / 组队 / 发消息"这类编排工具(对标 `INTERNAL_WORKER_TOOLS` 被移除)。
**理由:编排权集中在 leader,worker 只干活、不能再组队。** 这是星型拓扑成立的前提。

**③ 结果回灌:伪装成用户消息(关键技巧)**
worker 是异步子 Agent,它办完后,结果**不是函数返回值**,而是包装成一条"伪用户消息"塞回 leader 的对话流:

```
<task-notification>
  task-id:  worker 的 id(leader 可用它继续派活)
  status:   completed / failed / killed
  result:   worker 的最终结论
</task-notification>
```

> **为什么伪装成用户消息**:这样 leader 的主循环**完全不需要为"等待异步 worker"写特殊逻辑**——它只是"又收到一条用户消息",照常进入下一轮 ReAct 循环(推理→行动→观察)。异步协作无缝融进同一个 `loop.py`。对标 §4.5-B。

### 2.3 这就是 Orchestrator-Workers 模式

leader = orchestrator,worker = workers。**五大模式里的这一个,在 M6 是字面实现,不是涌现。**

---

## 3. mailbox:网状拓扑(M7,Team 模型)

### 3.1 与协调器的区别

```
   协调器(星型)            mailbox(网状)
   worker 只对 leader 说话    teammate 之间直接通信
   单向回灌                   双向 send / receive
   有明确主理人               更对等、自组织
```

### 3.2 设计点

**① mailbox = 一个极简异步队列**(对标 73 行的 `mailbox.ts`):
- `send(msg)`:有人正在 `receive` 等待(filter 匹配)→ 直接唤醒;否则入队
- `receive(filter)`:有匹配的取走;没有 → **挂起等待**(返回可 await 的对象)
- `poll(filter)`:非阻塞瞅一眼

**② SendMessage 是唯一通信方式(强约束)**
"Agent 的普通文本输出对别的 Agent 不可见 —— 要沟通必须调 SendMessage 工具。"
> 这条约束让**每次协作都显式、可追踪**,而不是靠"自言自语"。对标 `SendMessageTool/prompt.ts`。

### 3.3 何时用哪套

| 选 | 当… |
|---|---|
| **协调器(星型)** | 任务有明确主理人,需要集中控制 + 综合(如:供应商调研,leader 统筹) |
| **mailbox(网状)** | 对等协作、Agent 间要互相喊话(如:几个 Agent 互相校验结果) |

> 本项目的业务场景(M11 供应商挖掘)**主用协调器**;mailbox 作为能力储备 + 学习网状拓扑用。

---

## 4. 安全带:让自主不等于失控

多 Agent 放大了"自主",必须配套兜底(M4/M5 已含):

| 机制 | 作用 | 对标 |
|---|---|---|
| `max_turns` | 单个 Agent 转太多圈强制停 | query.ts maxTurns |
| 递归 `depth` 守卫 | 子 Agent 套娃超深就拒绝 | isInForkChild |
| `abort` 信号(沿 RunContext 传播) | 一键取消,级联到所有衍生 Agent | abortController |

---

## 5. 全景:三种用法如何拼成"多 Agent 系统"

```
            用户请求
               │
               ▼
        loop.py(主 Agent · M4)
               │
   ┌───────────┼─────────────────────────┐
   │           │                         │
   ▼           ▼                         ▼
 直接答    派子 Agent(M5 递归)      开协调器(M6 星型)
            │  并行 gather              leader 派 worker
            │  隔离上下文               worker 回灌(伪用户消息)
            ▼                          │
        只回传结论 ◄────────────────────┘
               │
               │  (需要对等协作时)
               ▼
        mailbox(M7 网状)teammate 互相 send/receive
               │
               ▼
        综合 → 产出最终结果(M11 业务层组装)
```

---

## 6. 实现顺序与验证

| 迭代 | 实现 | 验证(做完的标准) |
|---|---|---|
| **M5** | `subagent.py` AgentTool | 主 Agent 派 2 个子 Agent,能并行跑、各自隔离、只回传结论;depth 超限被拒 |
| **M6** | `coordinator.py` | leader 派 2 worker → 收 task-notification → 综合;worker 调编排工具被拒 |
| **M7** | `mailbox.py` + send_message 工具 | A `send` → B `receive` 挂起被唤醒;跑通"A 让 B 查、B 回传"最小协作 |

> 每个迭代配一个 `examples/` demo + `tests/` 测试。三个做完,本项目的多 Agent 编排内核就完整了。

---

## 7. 与整体架构文档的分工

| 文档 | 讲多 Agent 的哪一面 |
|---|---|
| [`05-技术架构设计.md`](./05-技术架构设计.md) | 它**在哪一层**、和其他层怎么咬合(全局视角) |
| **本文** | 它**内部怎么设计**:递归/星型/网状三种机制的细节与理由(深挖视角) |
| Claude Code `docs/agent-orchestration.md` | **真实源码**怎么实现的(原理对标) |
