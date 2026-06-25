"""一次性验证脚本:用真实 Bedrock 跑"对比 Python 和 Rust",数派了几个子 Agent、嵌到几层。

改进前:这个任务炸出 11 个 Agent(1 主 + 2 子 + 8 孙)。
改进后(三道防线):预期收敛到 1 主 + 2 子 = 3 个,depth 不超过 1。

跑法:uv run python scripts/verify_m5_no_overspawn.py
"""

from __future__ import annotations

import asyncio

from orchestra.context import RunContext
from orchestra.loop import run_agent_turn
from orchestra.message import Message
from orchestra.providers import make_model
from orchestra.subagent import AgentTool
from orchestra.tool import ClockTool, ReadFileTool, ToolRegistry, WriteFileTool


async def main() -> None:
    model = make_model("bedrock")

    spawn_count = 0
    max_depth_seen = 0

    def notify(line: str) -> None:
        nonlocal spawn_count, max_depth_seen
        if "派出子 Agent" in line:
            spawn_count += 1
            # 行里形如 "...(depth=1): ..." —— 抠出 depth 数字
            try:
                d = int(line.split("depth=")[1].split(")")[0])
                max_depth_seen = max(max_depth_seen, d)
            except (IndexError, ValueError):
                pass
        print(line)

    def base_tools() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool(), WriteFileTool()])

    registry = base_tools()
    registry.register(AgentTool(model, base_tools, notify=notify))

    messages: list[Message] = [
        Message.user("分别研究一下 Python 和 Rust 各自的优点，然后帮我对比")
    ]

    print("=== 开始(真实 Bedrock) ===\n")
    result = await run_agent_turn(messages, model, registry, RunContext())

    print("\n=== 统计 ===")
    print(f"派出的子 Agent 总数: {spawn_count}(含主 Agent 共 {spawn_count + 1} 个)")
    print(f"最深嵌套层数 depth: {max_depth_seen}(0=没套娃, 1=只到子, 2=到孙)")
    print(f"\n=== 主 Agent 最终答案(前 400 字) ===\n{result[-1].content[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
