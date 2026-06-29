# 子 Agent 隔离机制：从哪里读、怎么运作、为什么能撑企业级

> 对标 Claude Code `src/tools/AgentTool/runAgent.ts` + `src/utils/forkedAgent.ts`  
> 本仓库实现：`context.py` · `subagent.py` · `loop.py`

---

## 一、入口：从哪里开始读

按这个顺序读，每一步只引入一个新概念：

```
1. context.py          ← "隔离是什么" —— RunContext + child()
2. subagent.py:143-195 ← "隔离怎么用" —— AgentTool.run()：派任务、递归、回传结论
3. loop.py:27-69       ← "循环长什么样" —— run_agent_turn()，子 Agent 跑的就是这个
4. subagent.py:212-220 ← "_final_conclusion()：结论怎么抽出来"
```

最核心的一行在 `subagent.py:181`：

```python
result_messages = await run_agent_turn(sub_messages, self._model, sub_registry, child_ctx)
```

这一行就是"多 Agent"的全部秘密：**子 Agent 不是新框架，是主循环的递归调用**。

---

## 二、完整数据流图

```
用户输入
   │
   ▼
┌──────────────────────────────────────────────┐
│  主 Agent (RunContext #0, depth=0)           │
│                                              │
│  messages = [system, user("分析这个项目")]   │
│                                              │
│  run_agent_turn(messages, model, registry,   │
│                 ctx#0)                       │
│       │                                      │
│       │  ReAct 循环：推理 → 行动 → 观察       │
│       │                                      │
│       │  模型决定派两个子 Agent:              │
│       │    spawn_agent("分析架构")            │
│       │    spawn_agent("检查测试覆盖")        │
│       │         │          │                 │
│       │    ┌────┘          └────┐            │
│       │    │  M4 并发批：同时跑  │            │
│       │    ▼                   ▼            │
└───────┼────────────────────────────────────-─┘
        │                        │
        ▼                        ▼
┌───────────────┐      ┌───────────────────┐
│ 子 Agent A    │      │ 子 Agent B         │
│ #1, depth=1   │      │ #2, depth=1        │
│               │      │                   │
│ messages=     │      │ messages=          │
│  [SUBAGENT    │      │  [SUBAGENT         │
│   SYSTEM,     │      │   SYSTEM,          │
│   "分析架构"] │      │   "检查测试覆盖"]  │
│               │      │                   │
│ read_state:   │      │ read_state:        │
│  独立副本 ①  │      │  独立副本 ②       │
│               │      │                   │
│ abort: ──────────────────────── 同一个！  │
│               │      │                   │
│  run_agent    │      │  run_agent         │
│  _turn(...)   │      │  _turn(...)        │
│               │      │                   │
│  [读了10个文件│      │  [跑了20个测试分析 │
│   调了6次工具 │      │   调了8次工具      │
│   中间过程    │      │   中间过程         │
│   全部留在    │      │   全部留在         │
│   自己这里]   │      │   自己这里]        │
│       │       │      │        │           │
│       ▼       │      │        ▼           │
│  "架构结论    │      │  "测试覆盖结论     │
│   一句话"     │      │   一句话"          │
└───────┬───────┘      └────────┬───────────┘
        │   _final_conclusion() │
        └──────────┬────────────┘
                   ▼
        父级拿到两条结论字符串
        追加到自己的 messages
        继续下一轮推理
```

---

## 三、child() 做了什么：三道隔离线

`context.py:79` 的 `child()` 方法是隔离的核心，三件事同时做：

```
父级 RunContext #0
├── abort (asyncio.Event)  ──────────────────────── 共享 ──┐
├── read_state: {a.py: ✓}  ── dict() 复制 ──────────────┐  │
├── depth: 0                                             │  │
├── agent_id: 0                                          │  │
└── parent_id: None                                      │  │
                                                         │  │
                          child()                        │  │
                             │                           │  │
                             ▼                           │  │
子级 RunContext #1            │                           │  │
├── abort              ◄─────────────────────────────────┼──┘ 控制面共享
├── read_state: {a.py: ✓} ◄──┘  独立副本，父级不可见      │    数据面隔离
├── depth: 1                                              │
├── agent_id: 1 (新分配)                                  │
└── parent_id: 0                                          │
                                                          │
子级读了 b.py、c.py：                                      │
  read_state: {a.py:✓, b.py:✓, c.py:✓}                   │
                                                          │
父级 read_state 仍然是：                                   │
  {a.py: ✓}  ← 完全不受影响 ✓                              │
                                                          │
父级 abort.set() ──────────────────────────────────────► 子级立刻感知，停止
```

| 字段 | child() 行为 | 原因 |
|------|-------------|------|
| `abort` | **共享同一个对象** | 父级取消，所有子 Agent 必须一起停 |
| `read_state` | **复制一份独立副本** | 可变状态，共享会互相污染 |
| `depth` | **+1** | 知道自己嵌套几层，防无限套娃 |
| `agent_id` | **新分配** | 独立身份，日志追踪 / M7 寻址 |
| `parent_id` | **记住父级 id** | 链路追踪，知道"我爹是谁" |

---

## 四、父子通信：只有一个边界

父子之间没有共享队列、没有事件总线、没有回调链。通信边界就是一次函数调用：

```
父级
  │
  │  AgentTool.run() 调用：
  │
  ├─► 传入：task 字符串（一句话描述子任务）
  │         sub_messages（干净的：系统提示 + task，不含父级历史）
  │         child_ctx（隔离的上下文）
  │         sub_registry（收窄的工具集）
  │
  │  子 Agent 在自己的小本子上跑完整个 ReAct 循环
  │  （可能跑 10 轮、调几十次工具、读几百行文件——父级完全不知道）
  │
  ◄─ 返回：一条字符串（最后一条 assistant 消息的文本）
  │         ← _final_conclusion() 从 result_messages 里抽出来的
  │
  父级把这条字符串当作工具返回值追加到自己的 messages
  父级对子 Agent 的"探索过程"一无所知，也不需要知道
```

**子 Agent 的中间过程为什么要丢掉？**

- 子 Agent 可能调了 20 次工具，产出 5000 token 的中间消息
- 这些全部追加到父级 messages → 父级下一次请求就要把它们全发给模型
- token 爆炸 + 噪音淹没真正的结论
- 只传结论（通常 100-200 token）：父级 context 保持干净，预算可控

---

## 五、防过度拆分：三道防线

模型天然倾向于"遇事就派子 Agent"——三道防线按层次递进：

```
防线①（治本）：工具白名单
───────────────────────────
子 Agent 工具集里默认没有 spawn_agent。
没有锤子，自然不会到处找钉子。
代码：subagent.py:177-178
  if not self._allow_nesting:
      sub_registry = _without_tool(sub_registry, self.name)

对标 Claude Code: constants/tools.ts 的 ALL_AGENT_DISALLOWED_TOOLS


防线②（提示纪律）：系统提示明确禁止
───────────────────────────────────────
SUBAGENT_SYSTEM 里写：
  "Do NOT spawn new sub-agents — you are the worker."
代码：subagent.py:58
对标 Claude Code: forkSubagent.ts 的 "execute directly"


防线③（认知引导）：工具描述里写"何时不该用"
──────────────────────────────────────────────
spawn_agent 的 description 里有一段：
  "When NOT to use: simple work is faster done yourself —
   read a specific file with read_file, check time with now..."
代码：subagent.py:85-87
对标 Claude Code: AgentTool/prompt.ts:232


兜底（万一开了 allow_nesting）：depth 上限
──────────────────────────────────────────
DEFAULT_MAX_DEPTH = 2，超过就拒绝并返回错误字符串。
代码：subagent.py:147-151
```

---

## 六、并行：白捡的，不需要额外代码

```python
# is_concurrency_safe = True（subagent.py:92）
# ↓
# M4 的 partition_tool_calls 把所有 safe=True 的工具调用放进同一个并发批
# ↓
# asyncio.gather 同时跑
```

主 Agent 在一轮里调了两次 `spawn_agent`，M4 看到两个都是 `safe=True`，自动并发：

```
主 Agent 第 N 轮
  ├── spawn_agent("子任务A")  ─┐
  └── spawn_agent("子任务B")  ─┴─► asyncio.gather → 同时跑，耗时 = max(A,B)
```

不需要写任何"多 Agent 调度"代码，这是 M4 read-concurrent 设计带来的免费红利。

---

## 七、为什么这套设计能撑企业级产品

Claude Code 在生产环境运行千万次任务，这套隔离机制是稳定性的根基。原因有三：

**1. 故障隔离**  
子 Agent 崩溃、超时、输出垃圾——父级只拿到一条字符串，可以检查、可以重试、可以降级。子 Agent 的失败不会破坏父级的 messages 状态。

**2. 可变状态不泄漏**  
`read_state` 复制隔离确保：子 Agent 读了一堆文件，父级的"文件视图"不受影响。这避免了最难排查的 bug 类型——"我明明没读 X，为什么缓存里有 X"。

**3. 取消传播可靠**  
`abort` 共享一个对象，父级 `abort.set()` 一行代码，所有子 Agent（无论嵌套几层）在下一次检查时立刻停止。不需要广播，不需要遍历子 Agent 列表。

**Claude Code 额外做的（本仓库 N/A 的部分）：**  
- `setAppState: () => {}` —— 子 Agent 不能写 React UI 全局状态（本仓库无 UI，不需要）  
- `createChildAbortController` —— 子 abort 独立但父级 signal 单向传播（本仓库直接共享，效果等价）  
- `buildForkedMessages` + `cache_control` —— prompt cache 共享前缀（需要 API 支持，M9 再做）

核心设计理念：**控制面共享（能统一取消），数据面隔离（互不污染），身份独立（可追踪）**。三条原则，其他都是推论。

---

## 八、启动到第一条 prompt 的完整调用链

`uv run orchestra chat` 敲下回车，到用户第一条消息真正发给模型，经过这些函数：

```
uv run orchestra chat
│
├── pyproject.toml [tool.poetry.scripts] orchestra = "orchestra.cli:main"
│
▼
cli.py · main()                                          cli.py:73
│  sys.argv[1] == "chat" → asyncio.run(_chat())
│
▼
cli.py · _chat()                                         cli.py:35
│
├── make_model("bedrock")                                providers/__init__.py
│   └── BedrockModel.__init__()                          providers/bedrock.py
│       └── boto3.client("bedrock-runtime")              (AWS SDK，此处只建连接)
│
├── def base_tools() → ToolRegistry(                     cli.py:47
│       [ReadFileTool(), ClockTool(), WriteFileTool()])   tool.py
│
├── registry = base_tools()                              cli.py:53
│
├── AgentTool.__init__(                                  subagent.py:108
│     model, base_tools,
│     subagent_tools=["read_file","now"],
│     notify=print
│   )
│   └── 存下 _model / _build_registry / _notify 等字段
│
├── registry.register(agent_tool)                        tool.py:52
│
└── run_chat_loop(model, registry)                       loop.py:76
    │
    ├── ctx = RunContext()                                context.py:59
    │   └── abort=asyncio.Event(), read_state=ReadFileState()
    │       depth=0, agent_id=0, parent_id=None
    │
    ├── messages = []
    │
    └── while True:
        │
        ├── input("你 > ")                               ← 用户在这里输入第一条 prompt
        │
        ├── messages.append(Message.user(prompt))        message.py
        │
        └── run_agent_turn(messages, model, registry,    loop.py:27
                           ctx, on_event=...)
            │
            ├── tool_schemas = [t.name/description/input_schema
            │                   for t in registry.all()]
            │   └── 此时 schemas = [read_file, now, write_file, spawn_agent]
            │
            └── ── ReAct 循环第 1 轮 ──
                │
                ├── model.complete(messages, tools=tool_schemas)
                │   └── BedrockModel.complete()           providers/bedrock.py
                │       ├── _to_bedrock_messages(messages) ← 格式转换
                │       ├── client.converse(...)           ← 真正的 API 请求（第一次）
                │       └── _from_bedrock_response(...)    ← 解析回 Message
                │
                ├── messages.append(reply)
                │
                ├── if not reply.tool_calls → return      ← 纯回答，循环结束
                │
                └── run_tools(reply.tool_calls, registry, ctx)   orchestration.py:98
                    └── ... (见第八节调用树)
```

---

## 九、函数调用关系图（含子 Agent 递归）

从用户第一条 prompt 触发工具调用，到子 Agent 并行跑完回传结论，完整的调用栈：

```
cli.py
└── main()
    └── run_chat_loop()                          loop.py:76
        │   维护 messages[]，每轮追加
        └── run_agent_turn()                     loop.py:27
            │   ReAct 循环（最多 max_turns 轮）
            │
            ├── model.complete(messages, tools)  每轮：把历史发给模型
            │   └── BedrockModel.complete()      providers/bedrock.py
            │       └── (AWS Bedrock API)
            │
            └── run_tools(reply.tool_calls, ...)  orchestration.py:98
                │   异步生成器：按批 yield tool_result
                │
                ├── partition_tool_calls()        orchestration.py:35
                │   └── 按 is_concurrency_safe 切成连续批次
                │       [Read,Read,Write,Read] → [[R,R],[W],[R]]
                │
                ├── ── 只读批（len>1）──
                │   _run_read_batch()             orchestration.py:73
                │   ├── asyncio.Semaphore(10)     限并发上限
                │   ├── asyncio.create_task() × N 全部排上事件循环
                │   └── as_completed()            谁先好谁先 yield
                │       └── _run_one()            orchestration.py:63
                │           └── tool.run(input, ctx)
                │
                └── ── 写批 / 单个 ──
                    _run_one()                    orchestration.py:63
                    └── tool.run(input, ctx)
                        │
                        │  当 tool = AgentTool（spawn_agent）时：
                        ▼
                    AgentTool.run()               subagent.py:143
                    │
                    ├── ctx.child()               context.py:79
                    │   ├── abort      共享        ← 控制面
                    │   ├── read_state 复制        ← 数据面隔离
                    │   ├── depth+1               ← 套娃守卫
                    │   ├── agent_id   新分配      ← 独立身份
                    │   └── parent_id  = ctx.agent_id
                    │
                    ├── sub_messages = [SUBAGENT_SYSTEM, user(task)]
                    │   └── 干净隔离：不含父级历史
                    │
                    ├── sub_registry = build_registry()
                    │   ├── _only_tools(...)       按白名单收窄
                    │   └── _without_tool("spawn_agent")  防线①
                    │
                    ├── run_agent_turn(            ← ★ 递归：跑同一个循环
                    │     sub_messages,
                    │     model,
                    │     sub_registry,
                    │     child_ctx
                    │   )
                    │   └── （子 Agent 自己的完整 ReAct 循环，同上树）
                    │       可能跑 N 轮，调 M 个工具，读 K 个文件
                    │       所有中间过程留在 sub_messages 里，父级看不到
                    │
                    └── _final_conclusion(result_messages)  subagent.py:212
                        └── 倒序找最后一条有文本的 assistant 消息
                            只把这一条字符串返回给父级
```

### 两个子 Agent 并行时的调用时序

```
时间轴 ──────────────────────────────────────────────────────────►

run_agent_turn[父]
  │
  ├─ model.complete()          ← 模型一次返回两个 spawn_agent 调用
  │
  └─ run_tools([spawn_A, spawn_B])
       │
       partition_tool_calls()  → [[spawn_A, spawn_B]]  (都是 safe=True，同一批)
       │
       _run_read_batch()
       ├─ create_task(AgentTool.run(A))  ─┐
       └─ create_task(AgentTool.run(B))  ─┤ 同时排上事件循环
                                          │
              ┌────────────────────────────┴──────────────────────┐
              │ 子 Agent A                    │ 子 Agent B         │
              │ run_agent_turn(sub_msgs_A)    │ run_agent_turn(sub_msgs_B)│
              │   model.complete() → turn1   │   model.complete() → turn1│
              │   run_tools([read_file...])   │   run_tools([read_file...])│
              │   model.complete() → turn2   │   model.complete() → turn2│
              │   ...                        │   ...              │
              │   _final_conclusion()        │   _final_conclusion()     │
              └──────────────┬───────────────┴──────┬─────────────┘
                             │  as_completed():      │
                             │  谁先好谁先 yield      │
                             ▼                       ▼
              tool_result(A: "架构结论")   tool_result(B: "测试结论")
                             │                       │
                             └───────────┬───────────┘
                                         ▼
                              追加到父级 messages[]
                              父级下一轮 model.complete() 拿到两条结论
```

### 关键模块职责一览

```
context.py      RunContext      隔离容器：child() 是隔离边界
                ReadFileState   可变状态：随 child() 复制，互不污染

loop.py         run_agent_turn  ReAct 主循环，父子复用同一函数（递归）
                run_chat_loop   REPL 外壳，维护跨轮 messages

orchestration.py partition_tool_calls  按 safe 标签切批次
                 run_tools             批次调度入口（异步生成器）
                 _run_read_batch       并发执行 + Semaphore 限流

subagent.py     AgentTool.run   派任务 → 隔离上下文 → 递归 → 抽结论
                _final_conclusion  从 result_messages 里取最后一条 assistant
                _without_tool   从 registry 剔除 spawn_agent（防线①）

tool.py         ReadFileTool    读文件后写入 ctx.read_state（演示可变状态归属）
                ToolRegistry    工具注册表，按名字查 Tool 实例
```
