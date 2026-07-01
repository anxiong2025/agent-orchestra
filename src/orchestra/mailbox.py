"""M7 · Agent 间通信 —— mailbox(收件箱队列)。

对标 Claude Code: src/utils/mailbox.ts(全文才 73 行)
讲清的原理: 通信机制本身极简(一个队列),复杂度都在"谁能给谁发"的上层。

设计要点:
  · 每个 Agent 一个 Mailbox —— 自己的收件箱,别人往里放,自己来取。
  · send 时:有人正在 receive() 挂起等待 → 直接唤醒(不走队列,更快);
             没人等 → 入队,等下次 receive() 来取。
  · receive():队列有消息就立刻返回;没有就挂起,等 send() 唤醒。
  · poll():非阻塞,有消息就取,没有返回 None —— 给"顺便瞅一眼"用。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Mailbox:
    """一个 Agent 的收件箱。线程不安全，asyncio 单线程内使用。"""

    # 消息队列：send 时没人等就放这里，receive 时先从这里取。
    _queue: deque[str] = field(default_factory=deque)
    # 正在挂起等待的 receiver 列表：每个元素是一个 Future，
    # send 时直接往 Future 里塞结果来唤醒对方。
    _waiters: list[asyncio.Future[str]] = field(default_factory=list)

    def send(self, msg: str) -> None:
        """放一条消息进收件箱。

        有人正在 receive() 挂起 → 直接唤醒那个人，消息不过队列。
        没人等              → 消息入队，等下次 receive()/poll() 来取。
        """
        # 清理已经取消的 waiter（比如 receive() 的调用方被 abort 了）
        self._waiters = [w for w in self._waiters if not w.done()]

        if self._waiters:
            # 有人在等：取出最早的那个 waiter，把消息直接塞给它
            waiter = self._waiters.pop(0)
            waiter.set_result(msg)
        else:
            # 没人等：消息排队，等下次来取
            self._queue.append(msg)

    async def receive(self) -> str:
        """等待并取出下一条消息。队列空就挂起，直到有人 send()。"""
        if self._queue:
            # 队列里已经有消息，直接取
            return self._queue.popleft()

        # 队列空：注册一个 Future，挂起等待 send() 来唤醒
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[str] = loop.create_future()
        self._waiters.append(waiter)
        return await waiter  # 挂起，直到 send() 调用 waiter.set_result()

    def poll(self) -> str | None:
        """非阻塞地取一条消息。有就取，没有返回 None。"""
        return self._queue.popleft() if self._queue else None
