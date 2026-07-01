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
    (
        "M3",
        "第一个工具:ReAct 循环(推理→行动→观察)+ maxTurns(tool.py / context.py / loop.py)",
        True,
    ),
    ("M4", "多工具并发:读并发/写独占(orchestration.py)", True),
    ("M5", "子 Agent:递归 + 上下文隔离(subagent.py)", True),
    ("M6", "协调器:Orchestrator-Workers(coordinator.py)", True),
    ("M7", "Agent 间通信:mailbox + SendMessage(mailbox.py)", True),
    ("M8", "评估-优化循环(examples/)", False),
    ("M9", "多 provider 切换(providers/)", False),
    ("M10", "调研工具 + 溯源/防幻觉", False),
    ("M11", "业务编排:老外挖中国供应商", False),
]


async def _chat() -> None:
    """chat 入口:造 model + 工具 + 提示语,把对话循环交给 loop.py。"""
    from orchestra.loop import run_chat_loop
    from orchestra.providers import make_model
    from orchestra.subagent import AgentTool
    from orchestra.tool import ClockTool, ReadFileTool, ToolRegistry, WriteFileTool

    model = make_model("bedrock")

    # 子 Agent 能用的基础工具(读/写/查时间)。用工厂而非现成实例:每个子 Agent
    # 拿到独立的一份。注意这里【不】放 spawn_agent —— 子 Agent 默认就没有"派分身"
    # 的锤子(防线①治本)。即便放了,AgentTool(allow_nesting=False)也会自动剔除它。
    def base_tools() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool(), WriteFileTool()])

    # M5:主 Agent 的工具 = 基础工具 + spawn_agent(派子 Agent,自身递归复用主循环)。
    # ⭐ 子 Agent 工具按白名单收窄:只给只读工具(read_file/now),【不给 write_file】。
    #    研究/总结类子任务不该写文件 —— 给了模型就手痒反复写,单轮飙到 60s 还停不下来。
    registry = base_tools()
    registry.register(
        AgentTool(
            model,
            base_tools,
            subagent_tools=["read_file", "now"],
            notify=lambda s: print(s),
        )
    )
    print(
        "Agent Orchestra · chat(M5 多 Agent,可派子 Agent 并行办子任务,真实 Claude via Bedrock)"
    )
    print("它能读/写文件、查时间、派子 Agent 分头办活;记得上文;Ctrl-C 退出。")
    print(
        "试试:分别研究 Python 和 Rust 的优点然后对比 / "
        "同时读 README.md 和 pyproject.toml\n"
    )
    await run_chat_loop(model, registry)


async def _coordinator() -> None:
    """coordinator 入口：leader 派 worker 并行办活，worker 结果通过 mailbox 回灌。"""
    from orchestra.coordinator import WorkerTool
    from orchestra.loop import run_chat_loop
    from orchestra.providers import make_model
    from orchestra.tool import ClockTool, ReadFileTool, ToolRegistry

    model = make_model("bedrock")

    # worker 只能用只读工具，不给 write_file（研究任务不需要写文件）
    def worker_tools() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool()])

    registry = ToolRegistry([ReadFileTool(), ClockTool()])
    registry.register(
        WorkerTool(
            model,
            worker_tools,
            worker_tools=["read_file", "now"],
            notify=lambda s: print(s),
        )
    )
    print("Agent Orchestra · coordinator(M6 Orchestrator-Workers,派 worker 并行干活)")
    print("试试：同时研究 src/orchestra/loop.py 和 src/orchestra/tool.py 各自的职责\n")
    await run_chat_loop(model, registry)


async def _team() -> None:
    """team 入口：两个对等 Agent 用 send_message 网状互发消息(M7,对比 M6 的星型)。

    A = 你在 REPL 里操控的那个;B = 后台常驻的 peer,一直挂在 mailbox.receive() 上
    等消息,收到就跑一轮、决定要不要用 send_message 回发。两者共享同一份 ctx.directory
    (寻址花名册),所以能互相按 agent_id 找到对方 —— 跟 M6 worker→leader 单向回灌不同,
    这里谁都能先开口。
    """
    from orchestra.context import RunContext
    from orchestra.loop import run_agent_turn, run_chat_loop
    from orchestra.message import Message
    from orchestra.providers import make_model
    from orchestra.tool import ClockTool, ReadFileTool, SendMessageTool, ToolRegistry

    model = make_model("bedrock")

    ctx_a = RunContext()
    ctx_b = RunContext(directory=ctx_a.directory)  # 共享花名册,才能互相寻址

    def peer_tools() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool(), SendMessageTool()])

    peer_system = (
        f"You are agent #{ctx_b.agent_id}, one peer in a team of equals — there is no "
        'leader here. Incoming messages are wrapped as <message from="ID">...</message>; '
        "ID is the sender's numeric agent id. Use your tools to actually complete what's "
        "asked, then reply with send_message addressed to that same ID. Keep replies concise."
    )

    async def _run_peer_b() -> None:
        registry_b = peer_tools()
        messages_b: list[Message] = [Message.system(peer_system)]
        while True:
            incoming = await ctx_b.mailbox.receive()
            print(f"\n[peer B(#{ctx_b.agent_id}) 收到消息,处理中...]")
            messages_b.append(Message.user(incoming))
            await run_agent_turn(messages_b, model, registry_b, ctx_b, max_turns=5)
            print(f"[peer B(#{ctx_b.agent_id}) 处理完毕,继续监听]\n")

    # 长期存活的后台任务:保留引用,退出 REPL 时显式取消(不留悬空协程)。
    peer_task = asyncio.create_task(_run_peer_b())

    print("Agent Orchestra · team(M7 网状通信,两个对等 Agent 用 send_message 互相喊话)")
    print(f"你是 agent #{ctx_a.agent_id};后台常驻 peer 是 agent #{ctx_b.agent_id}。")
    print(f"试试:用 send_message 给 agent {ctx_b.agent_id} 发条消息,看它回你\n")
    try:
        await run_chat_loop(model, peer_tools(), ctx=ctx_a)
    finally:
        peer_task.cancel()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        asyncio.run(_chat())
        return

    if len(sys.argv) > 1 and sys.argv[1] == "coordinator":
        asyncio.run(_coordinator())
        return

    if len(sys.argv) > 1 and sys.argv[1] == "team":
        asyncio.run(_team())
        return

    print("Agent Orchestra —— 多 Agent 编排引擎(学习导向,对标 Claude Code)\n")
    next_todo: str | None = None
    for tag, title, done in MILESTONES:
        mark = "✅" if done else "⬜"
        print(f"  {mark} {tag}  {title}")
        if not done and next_todo is None:
            next_todo = f"{tag} —— {title}"
    print()
    print("💬 现在就能体验:")
    print("   uv run orchestra chat        —— M5 子 Agent(派分身并行办活)")
    print(
        "   uv run orchestra coordinator —— M6 协调器(leader 派 worker,结果通过 mailbox 回灌)"
    )
    print(
        "   uv run orchestra team        —— M7 网状通信(两个对等 Agent 用 send_message 互相喊话)"
    )
    print()
    print()
    if next_todo:
        print(f"👉 下一步: {next_todo}")
        print("   打开对应模块,删掉顶部的 raise NotImplementedError,按 TODO 实现。")
        print("   详细拆解见 specs/07-迭代开发计划.md。")
    else:
        print("🎉 全部迭代完成 —— 你已复刻 Claude Code 的多 Agent 编排核心。")
