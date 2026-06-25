"""M4 · 工具编排 —— 读并发 / 写独占 + 流式。⭐ 整个项目最该吃透的一节。

对标 Claude Code: src/services/tools/toolOrchestration.ts(partitionToolCalls 行 91 + 并发/串行)
讲清的原理: 模型一轮可能要调好几个工具。不让调度器去猜哪些能并发,而是让每个工具
            自己声明 is_concurrency_safe —— 调度器只按这个标签分批:
              · 连续的只读工具 → 合成一批,gather 并发跑(有上限,默认 10)
              · 写工具         → 自己独占一批,串行排队
            "多 Agent 能并行"不是专门写的,是子 Agent 的 AgentTool 被标 safe=True、
            从而落入"只读并发批"的副产品(M5 见分晓)。

为什么并发安全: 模型一轮里的多个工具结果,可以按【任意顺序】回传给模型 ——
            provider 用 tool_call_id 配对,不靠位置。所以这里"谁先跑完先 yield"
            (完成顺序 ≠ 原始顺序)对模型完全无害。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from orchestra.context import RunContext
from orchestra.message import Message, ToolCall
from orchestra.tool import ToolRegistry

# 对标 toolOrchestration.ts:8 的默认上限 10:只读批里最多同时跑这么多。
MAX_CONCURRENCY = 10


def _is_concurrency_safe(call: ToolCall, registry: ToolRegistry) -> bool:
    """这个调用能不能和别人并发?未知工具按"不安全"处理(独占一批,最保守)。"""
    tool = registry.get(call.name)
    return tool.is_concurrency_safe if tool is not None else False


def partition_tool_calls(
    calls: list[ToolCall], registry: ToolRegistry
) -> list[list[ToolCall]]:
    """按 is_concurrency_safe 把一轮的工具调用切成【连续批次】。

    规则:连续的只读 → 合成一批;任何写工具 → 自己独占一批。
    例:[Read, Read, Write, Read] -> [[Read,Read], [Write], [Read]]

    注意是"连续"切分,不是"把所有读归一批":写工具会打断只读批,保住
    "读→写→读"的先后语义(否则把后面的读提到写前面跑就乱了)。
    """
    batches: list[list[ToolCall]] = []
    read_batch: list[ToolCall] = []

    for call in calls:
        if _is_concurrency_safe(call, registry):
            read_batch.append(call)
        else:
            if read_batch:  # 先把攒着的只读批收尾
                batches.append(read_batch)
                read_batch = []
            batches.append([call])  # 写工具:自己独占一批

    if read_batch:
        batches.append(read_batch)
    return batches


async def _run_one(call: ToolCall, registry: ToolRegistry, ctx: RunContext) -> Message:
    """跑一个工具,把结果包成可回灌的 tool_result 消息。"""
    tool = registry.get(call.name)
    if tool is None:
        result = f"错误:未知工具 {call.name}"
    else:
        result = await tool.run(call.input, ctx)
    return Message.tool_result(call.id, result)


async def _run_read_batch(
    batch: list[ToolCall],
    registry: ToolRegistry,
    ctx: RunContext,
    max_concurrency: int,
) -> AsyncIterator[Message]:
    """只读批:并发跑,Semaphore 限并发,谁先好谁先 yield(边完成边出结果)。"""
    sem = asyncio.Semaphore(max_concurrency)

    async def _guarded(call: ToolCall) -> Message:
        async with sem:  # 限流:同时最多 max_concurrency 个在真正执行
            return await _run_one(call, registry, ctx)

    # 一次性把整批排上事件循环;信号量负责把"同时在跑的"压到上限内。
    tasks = [asyncio.create_task(_guarded(c)) for c in batch]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        # 上游提前 break(如 abort)时,别把没跑完的任务漏在后台。
        for t in tasks:
            if not t.done():
                t.cancel()


async def run_tools(
    calls: list[ToolCall],
    registry: ToolRegistry,
    ctx: RunContext,
    *,
    max_concurrency: int = MAX_CONCURRENCY,
) -> AsyncIterator[Message]:
    """异步生成器:按分批规则跑完一轮所有工具,边完成边 yield tool_result 消息。

    · 只读批(>1 个)→ 并发 gather,完成顺序 yield
    · 写批 / 单个工具 → 串行 await
    """
    for batch in partition_tool_calls(calls, registry):
        if ctx.abort.is_set():  # 级联取消:被叫停就别再开新批(M5 用得多)
            return

        if len(batch) > 1:
            # 多个只读 → 真正并发。这是 M4 的看点。
            async for tool_msg in _run_read_batch(
                batch, registry, ctx, max_concurrency
            ):
                yield tool_msg
        else:
            # 单个调用(写工具独占批,或落单的只读)→ 串行,语义最简单。
            yield await _run_one(batch[0], registry, ctx)
