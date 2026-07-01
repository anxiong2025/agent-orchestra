"""M7 测试 —— SendMessageTool:网状通信(任意 Agent 按 agent_id 互相喊话)。

对比 M6(coordinator.py 的 WorkerTool):worker 只能【单向】回灌给 leader;
这里验证的是【双向、对等】—— A 能主动找 B,B 也能主动找 A,谁都不是谁的上级。

测试顺序:
  1. 寻址:send_message 能把消息塞进"指定 agent_id"的收件箱,而不是自己的。
  2. 找不到人:agent_id 不存在时,返回明确错误,不崩。
  3. 唤醒:B 正在 receive() 挂起等待时,A 一 send_message,B 立刻被唤醒。
  4. 最小协作闭环(对标 spec §6):A 派活给 B,B 在自己的 ReAct 循环里通过
     inject_pending_notifications() "看见"这条消息并回复,A 下一轮"看见"回复。
"""

from __future__ import annotations

import asyncio

from orchestra.context import RunContext
from orchestra.loop import run_agent_turn
from orchestra.message import Message, ToolCall
from orchestra.model import MockModel
from orchestra.tool import SendMessageTool, ToolRegistry


def _peers() -> tuple[RunContext, RunContext]:
    """造两个共享同一张花名册(directory)但互不隶属的对等 Agent 上下文。"""
    ctx_a = RunContext()
    ctx_b = RunContext(directory=ctx_a.directory)
    return ctx_a, ctx_b


# ── 1. 寻址:发给 B,进的是 B 的收件箱 ──────────────────────────────────────


async def test_send_message_delivers_to_target_mailbox():
    ctx_a, ctx_b = _peers()
    tool = SendMessageTool()

    out = await tool.run({"to": ctx_b.agent_id, "message": "帮我查一下 X"}, ctx_a)

    assert f"agent {ctx_b.agent_id}" in out
    # 消息进了 B 的收件箱,A 自己的收件箱没有东西。
    delivered = ctx_b.mailbox.poll()
    assert delivered is not None
    assert f'from="{ctx_a.agent_id}"' in delivered
    assert "帮我查一下 X" in delivered
    assert ctx_a.mailbox.poll() is None


# ── 2. 找不到人:agent_id 不存在,返回错误字符串,不抛异常 ──────────────────


async def test_send_message_unknown_agent_returns_error():
    ctx_a, _ = _peers()
    tool = SendMessageTool()

    out = await tool.run({"to": 999999, "message": "有人吗"}, ctx_a)

    assert "Error" in out
    assert "999999" in out


# ── 3. B 挂起等待时,A 一发消息就唤醒 B ────────────────────────────────────


async def test_send_message_wakes_a_waiting_peer():
    ctx_a, ctx_b = _peers()
    tool = SendMessageTool()

    # 把 B 的 receive() 排上事件循环,让它先挂起(队列是空的)。
    task = asyncio.create_task(ctx_b.mailbox.receive())
    await asyncio.sleep(0)
    assert len(ctx_b.mailbox._waiters) == 1

    await tool.run({"to": ctx_b.agent_id, "message": "醒醒"}, ctx_a)

    result = await task
    assert "醒醒" in result
    assert f'from="{ctx_a.agent_id}"' in result


# ── 4. 最小协作闭环:A 派活给 B,B 回传,A 收到回传 ─────────────────────────


async def test_peer_round_trip_via_notifications():
    """对标 spec §6 M7 验收标准:"A 让 B 查、B 回传"最小协作。

    不是真并发跑两个循环(那需要两个 asyncio.create_task 长时间背景跑),
    而是分两步驱动,验证"注入-回复-再注入"这条链路本身是通的:
      ① A 跑一轮,调 send_message 把任务发给 B。
      ② B 跑一轮:第一件事就是 inject_pending_notifications() 捡到 A 的任务,
         模型看到后调 send_message 把结果发回 A。
      ③ A 再跑一轮:inject_pending_notifications() 捡到 B 的回复,模型综合结束。
    """
    ctx_a, ctx_b = _peers()

    registry_a = ToolRegistry([SendMessageTool()])
    registry_b = ToolRegistry([SendMessageTool()])

    # ① A:决定把任务发给 B。
    model_a_step1 = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="send_message",
                        input={"to": ctx_b.agent_id, "message": "帮我查一下 X"},
                    )
                ]
            ),
            Message.assistant(content="已把任务发给 B,等它回复。"),
        ]
    )
    messages_a: list[Message] = [Message.user("请协调 B 帮忙查 X")]
    await run_agent_turn(messages_a, model_a_step1, registry_a, ctx_a, max_turns=1)

    # B 的收件箱里已经躺着 A 的任务(还没被 B 读到)。
    assert ctx_b.mailbox is not None

    # ② B:第一轮开头 inject_pending_notifications 会把 A 的任务塞进 messages,
    #    B 的模型"看到"后决定回复 A。
    model_b = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(
                        id="s2",
                        name="send_message",
                        input={"to": ctx_a.agent_id, "message": "X 查到了,是个好东西"},
                    )
                ]
            ),
            Message.assistant(content="已回复 A。"),
        ]
    )
    messages_b: list[Message] = []
    await run_agent_turn(messages_b, model_b, registry_b, ctx_b, max_turns=2)

    # B 看到的第一条消息就是 A 发来的任务(通过 inject_pending_notifications 注入)。
    assert "帮我查一下 X" in messages_b[0].content
    assert f'from="{ctx_a.agent_id}"' in messages_b[0].content

    # ③ A:再跑一轮,inject_pending_notifications 捡到 B 的回复。
    model_a_step2 = MockModel(
        [Message.assistant(content="B 说 X 是个好东西,任务完成。")]
    )
    await run_agent_turn(messages_a, model_a_step2, registry_a, ctx_a, max_turns=1)

    # A 的历史里出现了 B 的回复(被当成一条"用户消息"注入,无缝融入 ReAct 循环)。
    reply_texts = [m.content for m in messages_a]
    assert any("X 查到了,是个好东西" in c for c in reply_texts)
    assert messages_a[-1].content == "B 说 X 是个好东西,任务完成。"
