"""M4 · 主循环 —— Agent 的心脏:想 → 做 → 看。

对标 Claude Code: src/query.ts 的 queryLoop(while(true));maxTurns 见 §2.6
讲清的原理: 这就是 Prompt Chaining 的本质 —— 结果回灌成下一轮输入,链是循环的副产品。

────────────────────────────────────────────────────────────────────────
TODO(M4): async def run_loop(messages, model, tools, ctx, max_turns) -> 异步生成器
    while True:
        reply = await model.complete(messages)   # 想
        yield reply
        if reply 没有工具调用: return            # 看:没活了 → 结束
        async for result in run_tools(reply.tool_calls, ctx):  # 做(用 M3)
            yield result
            把 result 回灌进 messages             # → 下一轮模型基于新结果决策
        if turn > max_turns:                      # 熔断:防死循环烧钱
            yield 一条"超过最大轮数"的消息; return

验证: ① MockModel 脚本化"先调工具→看到结果→再回答",能完整跑完。
      ② 让 MockModel 永远调工具,验证 max_turns 能刹住。
对标讲解见 docs/agent-orchestration.md 第 1、2.6 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M4): 删除这行,开始实现 run_loop。
raise NotImplementedError("M4: 实现主循环 run_loop(见本文件顶部 TODO)")
