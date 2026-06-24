"""命令行入口 —— `uv run orchestra`。

- `orchestra`        看当前进度看板、下一步做哪个迭代。
- `orchestra chat`   M3:ReAct Agent(能调工具读文件/查时间;走 Bedrock,用你本地 AWS 凭证)。

随着 M1–M11 完成,这里会从"看板 + chat"长成真正驱动 run_loop 的入口。
"""

from __future__ import annotations

import asyncio
import sys

# 迭代清单(与 ROADMAP.md 对应)。done 字段你做完一个就改成 True。
MILESTONES: list[tuple[str, str, bool]] = [
    ("M0", "项目骨架(uv / lint / test / 占位模块)", True),
    ("M1", "直接调真实 LLM:单轮对话(providers/bedrock.py / cli.py chat)", True),
    ("M2", "多轮对话循环:上下文记忆(loop.py)", True),
    ("M3", "第一个工具:ReAct 循环(推理→行动→观察)+ maxTurns(tool.py / context.py / loop.py)", True),
    ("M4", "多工具并发:读并发/写独占(orchestration.py)", False),
    ("M5", "子 Agent:递归 + 上下文隔离(subagent.py)", False),
    ("M6", "协调器:Orchestrator-Workers(coordinator.py)", False),
    ("M7", "Agent 间通信:mailbox + SendMessage(mailbox.py)", False),
    ("M8", "评估-优化循环(examples/)", False),
    ("M9", "多 provider 切换(providers/)", False),
    ("M10", "调研工具 + 溯源/防幻觉", False),
    ("M11", "业务编排:老外挖中国供应商", False),
]


async def _chat() -> None:
    """chat 入口:造 model + 工具 + 提示语,把对话循环交给 loop.py。"""
    from orchestra.loop import run_chat_loop
    from orchestra.providers import make_model
    from orchestra.tool import ClockTool, ReadFileTool, ToolRegistry

    model = make_model("bedrock")
    registry = ToolRegistry([ReadFileTool(), ClockTool()])  # M3:给它一双手
    print("Agent Orchestra · chat(M3 ReAct Agent,真实 Claude via Bedrock)")
    print("它能读文件/查时间;记得上文;Ctrl-C 退出。")
    print("试试:读一下 README.md 第一行 / 现在几点\n")
    await run_chat_loop(model, registry)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        asyncio.run(_chat())
        return

    print("Agent Orchestra —— 多 Agent 编排引擎(学习导向,对标 Claude Code)\n")
    next_todo: str | None = None
    for tag, title, done in MILESTONES:
        mark = "✅" if done else "⬜"
        print(f"  {mark} {tag}  {title}")
        if not done and next_todo is None:
            next_todo = f"{tag} —— {title}"
    print()
    print("💬 现在就能体验: uv run orchestra chat  —— ReAct Agent(M3,能调工具干活)")
    print()
    if next_todo:
        print(f"👉 下一步: {next_todo}")
        print("   打开对应模块,删掉顶部的 raise NotImplementedError,按 TODO 实现。")
        print("   详细拆解见 specs/07-迭代开发计划.md。")
    else:
        print("🎉 全部迭代完成 —— 你已复刻 Claude Code 的多 Agent 编排核心。")
