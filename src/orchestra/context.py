"""M2 · 运行上下文 —— 贯穿一次运行的状态 + 取消信号 + 递归深度。

对标 Claude Code: src/Tool.ts 的 ToolUseContext;abort 信号见 runAgent.ts §3.3
讲清的原理: 取消信号沿着上下文传播;递归深度让子 Agent 知道自己嵌套几层。

────────────────────────────────────────────────────────────────────────
TODO(M2):
  - RunContext: 至少包含
      * abort:取消信号(asyncio.Event 或一个简单 flag)
      * depth:递归深度(M5 子 Agent 用,每递归一层 +1)
  - 提供一个"派生子上下文"的方法(depth+1,可共享或新建 abort)——M5 会用到
对标讲解见 docs/agent-orchestration.md 第 1.3、3.3 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M2): 删除这行,开始定义 RunContext。
raise NotImplementedError("M2: 实现 RunContext(见本文件顶部 TODO)")
