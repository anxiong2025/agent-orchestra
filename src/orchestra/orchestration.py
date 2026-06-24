"""M3 · 工具编排 —— 读并发 / 写独占 + 流式。⭐ 整个项目最该吃透的一节。

对标 Claude Code: src/services/tools/toolOrchestration.ts(partitionToolCalls + 并发/串行)
讲清的原理: "多 Agent 能并行"不是专门写的,是"工具标记为 safe → 落入只读并发批"的副产品。

────────────────────────────────────────────────────────────────────────
TODO(M3):
  - partition_tool_calls(calls): 按 is_concurrency_safe 切成连续批次
      连续的只读 → 合成一批;任何写工具 → 自己独占一批
      例: [Read, Read, Write, Read] -> [[Read,Read], [Write], [Read]]
  - async def run_tools(calls, ctx): 异步生成器
      只读批 → asyncio.gather 并发(用 asyncio.Semaphore(10) 限并发)
      写批   → 一个个 await(串行)
      边完成边 yield(不要全跑完再返回)

⚠️ 故意留给你踩的坑: 第一次很可能"明明并发却一个个跑"。
   多半是某处用了阻塞调用、或忘了 gather。踩明白这个,才算真懂 asyncio。
对标讲解见 docs/agent-orchestration.md 第 2 节(精确到行)。
────────────────────────────────────────────────────────────────────────
"""

# 对标 toolOrchestration.ts:8 的默认上限 10(可后续做成可配置)
MAX_CONCURRENCY = 10

# TODO(M3): 删除这行,开始实现 partition_tool_calls / run_tools。
raise NotImplementedError("M3: 实现工具编排(见本文件顶部 TODO)")
