"""M5 · 子 Agent —— 递归 + 上下文隔离。

对标 Claude Code: src/tools/AgentTool/runAgent.ts(§3),结尾再调 query()
核心顿悟: 子 Agent = 带一份隔离的上下文,再调一次 run_loop(M4)。多 Agent 不是新框架。

────────────────────────────────────────────────────────────────────────
TODO(M5):
  - AgentTool(本身是个 Tool,is_concurrency_safe=True → 多个子 Agent 能并行!)
  - 它的 run 里:
      * 造一份隔离上下文(干净 messages,只带子任务 prompt;ctx.depth + 1)
      * 调 run_loop(递归!跑一个独立的 mini 循环)
      * 只把子 Agent 的"最终结论"返回父级(不是全过程 → 保护父级上下文)
  - 递归守卫: depth 超上限就拒绝再派(防无限套娃,对标 isInForkChild)
这里也涌现了 Routing(选派哪个子 Agent)。
对标讲解见 docs/agent-orchestration.md 第 3 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M5): 删除这行,开始实现 AgentTool。
raise NotImplementedError("M5: 实现子 Agent(见本文件顶部 TODO)")
