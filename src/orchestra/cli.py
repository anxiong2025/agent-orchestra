"""命令行入口 —— `uv run orchestra`。

这是骨架自带的、唯一现在就能跑的东西:它会告诉你当前进度、下一步做哪个迭代。
随着 M1–M11 完成,你可以把这里改成真正驱动 run_loop 的入口。
"""

from __future__ import annotations

# 迭代清单(与 ROADMAP.md 对应)。done 字段你做完一个就改成 True。
MILESTONES: list[tuple[str, str, bool]] = [
    ("M0", "项目骨架(uv / lint / test / 占位模块)", True),
    ("M1", "消息 + 模型抽象(message.py / model.py)", False),
    ("M2", "工具系统(tool.py / context.py)", False),
    ("M3", "工具编排:读并发/写独占 + 流式(orchestration.py)", False),
    ("M4", "主循环:想→做→看 + maxTurns(loop.py)", False),
    ("M5", "子 Agent:递归 + 上下文隔离(subagent.py)", False),
    ("M6", "协调器:Orchestrator-Workers(coordinator.py)", False),
    ("M7", "Agent 间通信:mailbox + SendMessage(mailbox.py)", False),
    ("M8", "评估-优化循环(examples/)", False),
    ("M9", "接入真实 LLM(AnthropicModel)", False),
    ("M10", "调研工具 + 溯源/防幻觉", False),
    ("M11", "业务编排:老外挖中国供应商", False),
]


def main() -> None:
    print("Agent Orchestra —— 多 Agent 编排引擎(学习导向,对标 Claude Code)\n")
    next_todo: str | None = None
    for tag, title, done in MILESTONES:
        mark = "✅" if done else "⬜"
        print(f"  {mark} {tag}  {title}")
        if not done and next_todo is None:
            next_todo = f"{tag} —— {title}"
    print()
    if next_todo:
        print(f"👉 下一步: {next_todo}")
        print("   打开对应模块,删掉顶部的 raise NotImplementedError,按 TODO 实现。")
        print("   详细拆解见 ROADMAP.md。")
    else:
        print("🎉 全部迭代完成 —— 你已复刻 Claude Code 的多 Agent 编排核心。")
