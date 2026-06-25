"""M3/M4 · 主循环 —— Agent 的"心脏":ReAct 循环(Reason → Act → Observe)+ maxTurns 熔断。

对标 Claude Code: src/query.ts 的 queryLoop(while True);maxTurns 见 §2.6
讲清的原理: 这就是 Prompt Chaining / ReAct 的本质 —— 工具结果回灌成下一轮输入,
            链是循环的副产品。"结束"由模型自决(它不再要工具 = 它觉得做完了)。
            能自主决策就可能陷死循环,所以 maxTurns 是必须的安全带。

M2 是无工具的纯对话;M3 在循环里加了"模型要不要调工具"的分支;
M4 把"串行跑工具"换成 orchestration.run_tools(读并发 / 写独占,边完成边回灌)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from orchestra.context import RunContext
from orchestra.message import Message
from orchestra.model import Model
from orchestra.orchestration import run_tools
from orchestra.tool import ToolRegistry

# 默认最大轮数:防止 Agent 反复调工具停不下来烧钱。
DEFAULT_MAX_TURNS = 10


async def run_agent_turn(
    messages: list[Message],
    model: Model,
    registry: ToolRegistry,
    ctx: RunContext,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    on_event: Callable[[str, Message], None] | None = None,
) -> list[Message]:
    """处理一次用户提问的完整"ReAct 循环(推理→行动→观察)"循环,原地追加到 messages 并返回。

    on_event(kind, msg): 可选回调,kind ∈ {"assistant","tool_result"},用于实时打印。
    """
    tool_schemas = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in registry.all()
    ]

    for _turn in range(max_turns):
        # 想:把整个历史(+工具清单)发给模型。
        reply = await model.complete(messages, tools=tool_schemas or None)
        messages.append(reply)
        if on_event:
            on_event("assistant", reply)

        # 看:模型没要求调工具 → 它觉得做完了,结束。
        if not reply.tool_calls:
            return messages

        # 做:把这一轮的工具调用交给编排器 —— 只读批并发、写批独占,边完成边回灌。
        # (M3 这里是逐个串行;M4 换成 run_tools 异步生成器。)
        async for tool_msg in run_tools(reply.tool_calls, registry, ctx):
            messages.append(tool_msg)
            if on_event:
                on_event("tool_result", tool_msg)
        # 回到循环顶:模型基于工具结果再决策。

    # 熔断:转太多圈还没收尾,强制结束并留个痕迹。
    limit_msg = Message.assistant(content=f"(已达最大轮数 {max_turns},强制结束)")
    messages.append(limit_msg)
    if on_event:
        on_event("assistant", limit_msg)
    return messages


# REPL 的 I/O 做成可注入,方便测试塞脚本。
ReadInput = Callable[[], Awaitable[str | None]]


async def run_chat_loop(
    model: Model,
    registry: ToolRegistry | None = None,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> None:
    """多轮对话外壳:维护累积历史,每轮跑一次 run_agent_turn(ReAct 循环(推理→行动→观察))。"""
    registry = registry or ToolRegistry()
    ctx = RunContext()
    messages: list[Message] = []

    def on_event(kind: str, msg: Message) -> None:
        if kind == "assistant" and msg.tool_calls:
            names = ", ".join(c.name for c in msg.tool_calls)
            print(f"\n[Agent 调用工具: {names}]")
        elif kind == "assistant" and msg.content:
            print(f"\nClaude > {msg.content}\n")

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "你 > ")).strip()
        except KeyboardInterrupt, EOFError:
            break
        if not user_input:
            continue

        messages.append(Message.user(user_input))
        await run_agent_turn(
            messages, model, registry, ctx, max_turns=max_turns, on_event=on_event
        )

    print("\n再见。")
