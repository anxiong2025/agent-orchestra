"""M3 · 运行上下文 —— 贯穿一次运行的状态 + 取消信号 + 递归深度。

对标 Claude Code: src/Tool.ts 的 ToolUseContext;abort 信号见 runAgent.ts §3.3
讲清的原理: 取消信号沿着上下文传播;递归深度让子 Agent 知道自己嵌套几层。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class RunContext:
    """一次运行的上下文。工具拿到它,能感知"是否该取消""嵌套几层"。"""

    # 取消信号:谁都能 .set() 它,工具/循环看到 is_set() 就该停。M3 先备着,M5 用得多。
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    # 递归深度:主 Agent=0,每派一层子 Agent +1。M5 用它做"套娃守卫"。
    depth: int = 0

    def child(self) -> RunContext:
        """派生子上下文(depth+1,共享同一个 abort)——M5 子 Agent 会用。"""
        return RunContext(abort=self.abort, depth=self.depth + 1)
