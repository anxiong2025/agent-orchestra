"""M2 · 工具系统 —— Agent 的"手"。

对标 Claude Code: src/Tool.ts(工具抽象、isConcurrencySafe)
讲清的原理: 工具自己声明能不能并发,而不是让调度器去猜(声明式并发的根)。

────────────────────────────────────────────────────────────────────────
TODO(M2):
  - Tool(抽象基类): name / description / `async def run(self, input, ctx) -> str`
  - 关键字段 is_concurrency_safe: 只读=True / 写=False(M3 分批要用)
  - ToolRegistry: 按名字注册和查找工具
  - 两个示例工具: ReadFileTool(safe=True)、WriteFileTool(safe=False)
对标讲解见 docs/agent-orchestration.md 第 2 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M2): 删除这行,开始定义 Tool / ToolRegistry / 示例工具。
raise NotImplementedError("M2: 实现工具系统(见本文件顶部 TODO)")
