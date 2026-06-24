"""M2 · 多轮对话循环 —— 让 Agent 记得上文。

对标 Claude Code: src/query.ts 的会话循环 —— 每轮把回复 append 回 messages,
                  下一轮把【整个历史】再发给模型。这就是"记忆"的本质。
讲清的原理: 上下文 = 一个不断增长的 Message 列表。没有数据库、没有魔法,
            "它记得"只是因为每轮都把全部历史一起发出去。

(M4 会在这个循环里加"想→做→看 + 工具",把它升级成真正的 Agent 循环。M2 先做无工具版。)

────────────────────────────────────────────────────────────────────────
TODO(M2): async def run_chat_loop(model) -> None
    messages = []                          # ① 记忆:一个贯穿全程的列表
    while True:
        user_input = 读一句输入             # ② input() 阻塞,用 asyncio.to_thread 包
        if 用户退出(EOF/Ctrl-C): break
        messages.append(user 消息)          # ③ 用户这句进历史
        reply = await model.complete(messages)  # ④ 把【整个历史】发出去(不是单句!)
        messages.append(reply)             # ⑤ 回复也进历史 → 下轮模型就"记得"
        打印 reply                          # ⑥ 回到 ②

验证: uv run orchestra chat
      你 > 我叫小明
      你 > 我叫什么?      ← 它应当答"你叫小明"(记住了 ⑤)
对标讲解见 specs/07-迭代开发计划.md 的 M2 一节(含架构图)。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio

from orchestra.message import Message
from orchestra.model import Model


async def run_chat_loop(model: Model) -> None:
    """M2 多轮对话:维护一个累积的 messages,每轮把整个历史发给模型。"""
    messages: list[Message] = []  # ① 记忆:贯穿全程、只增不减的列表

    while True:
        # ② 读一句输入。input() 阻塞,丢到线程里跑,别卡住事件循环。
        #    Ctrl-C / Ctrl-D 退出 → 跳出循环。
        try:
            user_input = (await asyncio.to_thread(input, "你 > ")).strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_input:
            continue

        messages.append(Message.user(user_input))  # ③ 用户这句进历史
        reply = await model.complete(messages)  # ④ 把【整个历史】发出去(不是单句!)
        messages.append(reply)  # ⑤ 回复也进历史 → 下轮模型就"记得"
        print(f"\nClaude > {reply.content}\n")  # ⑥ 打印,回到 ②

    print("\n再见。")
