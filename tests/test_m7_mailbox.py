"""M7 测试 —— Mailbox 三个方法的行为验证。

测试顺序对应使用场景：
  1. poll：非阻塞取消息（最简单，先验）
  2. send → poll：放进去能取出来
  3. receive：队列有消息直接返回
  4. receive 挂起 → send 唤醒（核心异步行为）
  5. send 时已有人在等，消息直接交过去不过队列
"""

from __future__ import annotations

import asyncio

from orchestra.mailbox import Mailbox


# ── 1. poll：空队列返回 None ─────────────────────────────────────

def test_poll_empty_returns_none():
    """队列为空时 poll() 返回 None，不报错。"""
    mb = Mailbox()
    assert mb.poll() is None


# ── 2. send → poll：放进去能取出来 ──────────────────────────────

def test_send_then_poll():
    """send 一条消息，poll 能取到，取完再 poll 返回 None。"""
    mb = Mailbox()
    mb.send("hello")
    assert mb.poll() == "hello"
    assert mb.poll() is None  # 取完了


def test_send_multiple_fifo():
    """多条消息按顺序取出（先进先出）。"""
    mb = Mailbox()
    mb.send("first")
    mb.send("second")
    mb.send("third")
    assert mb.poll() == "first"
    assert mb.poll() == "second"
    assert mb.poll() == "third"
    assert mb.poll() is None


# ── 3. receive：队列有消息直接返回 ──────────────────────────────

async def test_receive_with_existing_message():
    """队列里已经有消息，receive() 立刻返回，不挂起。"""
    mb = Mailbox()
    mb.send("已有消息")
    result = await mb.receive()
    assert result == "已有消息"


# ── 4. receive 挂起 → send 唤醒（核心异步行为）──────────────────

async def test_receive_waits_then_send_wakes_it():
    """receive() 发现队列空，挂起；之后 send() 把它唤醒。

    这是 Mailbox 最核心的异步行为：
      - asyncio.create_task 把 receive() 排上事件循环（还没跑）
      - send() 先跑，发现有人在等，直接唤醒
      - receive() 被唤醒，拿到消息
    """
    mb = Mailbox()

    # 把 receive() 排上事件循环，还没真正执行
    task = asyncio.create_task(mb.receive())

    # 让事件循环跑一下，receive() 开始执行并挂起（队列是空的）
    await asyncio.sleep(0)

    # 此时 receive() 已经挂起，_waiters 里有一个 Future
    assert len(mb._waiters) == 1

    # send 唤醒它
    mb.send("唤醒消息")

    # 等 task 跑完
    result = await task
    assert result == "唤醒消息"

    # send 直接交给了 waiter，_queue 里没有东西
    assert mb.poll() is None


# ── 5. send 时已有人等：消息不过队列直接交 ──────────────────────

async def test_send_bypasses_queue_when_waiter_exists():
    """有人在 receive() 等时，send 直接交消息，_queue 保持为空。"""
    mb = Mailbox()

    task = asyncio.create_task(mb.receive())
    await asyncio.sleep(0)  # 让 receive() 挂起

    mb.send("直达消息")

    result = await task
    assert result == "直达消息"
    # 消息直接交给 waiter，没有进 _queue
    assert len(mb._queue) == 0
    assert len(mb._waiters) == 0
