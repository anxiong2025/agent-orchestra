"""诊断脚本:给每个 Agent 的每一轮模型调用打时间戳,定位"为什么慢"。

它直接复刻 run_agent_turn 的循环,但在每次 model.complete 前后记时间,
这样能看到:主 Agent 跑了几轮、每个子 Agent 跑了几轮、每轮花多久、有没有调写工具。

跑法:uv run python -u scripts/diag_m5_timing.py
"""

from __future__ import annotations

import asyncio
import time

from orchestra.context import RunContext
from orchestra.message import Message
from orchestra.orchestration import run_tools
from orchestra.providers import make_model
from orchestra.subagent import AgentTool
from orchestra.tool import ClockTool, ReadFileTool, ToolRegistry, WriteFileTool

T0 = time.monotonic()


def stamp() -> str:
    return f"[{time.monotonic() - T0:6.1f}s]"


async def traced_turn(messages, model, registry, ctx, *, label, max_turns=10):
    """带 per-turn 计时的 run_agent_turn 复刻版。"""
    tool_schemas = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in registry.all()
    ]
    for turn in range(max_turns):
        t = time.monotonic()
        reply = await model.complete(messages, tools=tool_schemas or None)
        dt = time.monotonic() - t
        tools = ", ".join(c.name for c in reply.tool_calls) or "—"
        print(
            f"{stamp()} [{label}] 第{turn + 1}轮: 模型调用耗时 {dt:5.1f}s | "
            f"文本{len(reply.content)}字 | 工具调用: {tools}"
        )
        messages.append(reply)
        if not reply.tool_calls:
            return messages
        async for tool_msg in run_tools(reply.tool_calls, registry, ctx):
            messages.append(tool_msg)
    print(f"{stamp()} [{label}] ⚠️ 撞上 max_turns={max_turns} 熔断!")
    return messages


async def main() -> None:
    model = make_model("bedrock")

    def base_tools() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool(), WriteFileTool()])

    # 给子 Agent 用带计时的循环:猴补 AgentTool 让它的 run 走 traced_turn。
    sub_counter = {"n": 0}

    class TracedAgentTool(AgentTool):
        async def run(self, tool_input, ctx):
            self._n = sub_counter["n"] = sub_counter["n"] + 1
            label = f"子Agent#{self._n}"
            task = tool_input.get("task", "").strip()
            print(f"{stamp()} {label} 启动, task 长度 {len(task)} 字")
            child = ctx.child()
            sub_messages = [
                Message.system(
                    __import__(
                        "orchestra.subagent", fromlist=["SUBAGENT_SYSTEM"]
                    ).SUBAGENT_SYSTEM
                ),
                Message.user(task),
            ]
            # 修复后:子 Agent 工具按白名单收窄,只给只读工具,不给 write_file。
            full = base_tools()
            sub_reg = ToolRegistry(
                [t for t in full.all() if t.name in ("read_file", "now")]
            )
            res = await traced_turn(
                sub_messages, model, sub_reg, child, label=label, max_turns=10
            )
            for m in reversed(res):
                if m.role.value == "assistant" and m.content:
                    print(f"{stamp()} {label} 交回结论({len(m.content)}字)")
                    return m.content
            return "(无结论)"

    registry = base_tools()
    registry.register(TracedAgentTool(model, base_tools))

    messages = [Message.user("分别研究一下 Python 和 Rust 各自的优点，然后帮我对比")]
    print(f"{stamp()} === 主 Agent 开始 ===")
    await traced_turn(messages, model, registry, RunContext(), label="主Agent")
    print(f"{stamp()} === 全部完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
