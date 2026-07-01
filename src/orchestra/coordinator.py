"""M6 · 协调器 —— Orchestrator-Workers(编排与执行分离)。

对标 Claude Code: src/coordinator/coordinatorMode.ts(§4.5-A)
讲清的原理:
  1. 编排权集中在 leader —— leader 只派活 + 综合，自己不下场干具体活
  2. fire-and-forget 派发 —— dispatch_worker 立刻返回，worker 后台跑
  3. task-notification 回灌 —— worker 结果包装成 XML，注入 leader 的消息流
     对标 Claude Code §4.5-B 的 <task-notification> 机制

WorkerTool 和 AgentTool 的关键区别:
  AgentTool  (M5): 同步等待 —— run() 阻塞到子 Agent 跑完，直接返回结论
  WorkerTool (M6): 异步派发 —— run() 立刻返回"已派出"，worker 后台跑，
                   跑完往 ctx.mailbox（leader 的收件箱）发 task-notification，
                   由 loop.py 的 inject_pending_notifications() 在下一轮注入 messages[]
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from orchestra.context import RunContext
from orchestra.loop import DEFAULT_MAX_TURNS, run_agent_turn
from orchestra.message import Message
from orchestra.model import Model
from orchestra.subagent import _final_conclusion, _only_tools, _without_tool
from orchestra.tool import Tool, ToolRegistry

# Worker 系统提示：强调"你只执行，不编排"（防止 worker 自己再去派 worker）。
# 英文原因同 SUBAGENT_SYSTEM，参见 subagent.py 顶部注释。
WORKER_SYSTEM = (
    "You are a worker agent executing a well-scoped task assigned by a coordinator. "
    "Use the tools available to you and complete the task directly yourself. "
    "Do NOT spawn new sub-agents or dispatch new workers — you are the executor. "
    "When done, give a concise final result; do not narrate your process. "
    "Keep it under ~200 words."
)


def _format_notification(task: str, conclusion: str) -> str:
    """把 worker 结论包装成 <task-notification> XML 字符串。

    leader 收到后，loop.py 把它当成"用户消息"注入 messages[]，
    模型就会在下一轮"看到" worker 的结果 —— 无缝融入 ReAct 循环。

    对标 Claude Code §4.5-B 的 task-notification 格式。
    """
    return (
        "<task-notification>\n"
        f"Task: {task}\n"
        f"Result: {conclusion}\n"
        "</task-notification>"
    )


class WorkerTool(Tool):
    """把一个子任务 fire-and-forget 派给 worker；跑完往 leader 的 mailbox 发通知。

    is_concurrency_safe = True：
      和 AgentTool 一样，多个 dispatch_worker 调用落入 M4 的只读并发批，
      asyncio.create_task 同时排上事件循环 —— 多个 worker 自动并行。
    """

    name = "dispatch_worker"
    description = (
        "Dispatch a sub-task to a worker agent asynchronously. "
        "Returns immediately — the worker runs in the background. "
        "When the worker finishes, its result arrives as a <task-notification> message. "
        "Dispatch multiple workers in one turn to run them in parallel.\n"
        "When NOT to use: simple single-step work — do it directly with available tools. "
        "Parameter `task`: describe the worker's task in complete natural language."
    )
    # ⭐ True → 多个 dispatch_worker 落入 M4 并发批，所有 worker 同时派出
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The self-contained task for the worker agent, "
                    "as a complete natural-language description."
                ),
            }
        },
        "required": ["task"],
    }

    def __init__(
        self,
        model: Model,
        build_registry: Callable[[], ToolRegistry],
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        worker_tools: list[str] | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        """
        model:          worker 用的模型（通常和 coordinator 共用，省连接）。
        build_registry: 工具集工厂，每个 worker 拿独立一份。
        max_turns:      worker 自己 ReAct 循环的熔断上限。
        worker_tools:   worker 的工具白名单；None = 只去掉 dispatch_worker 本身。
        notify:         可选进度回调（CLI 打进度用；测试不传）。
        """
        self._model = model
        self._build_registry = build_registry
        self._max_turns = max_turns
        self._worker_tools = worker_tools
        self._notify = notify

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        task = tool_input.get("task", "").strip()
        if not task:
            return "Error: task cannot be empty."

        # child() 派生隔离上下文（abort 共享，read_state 复制，mailbox 新建）
        child_ctx = ctx.child()

        if self._notify:
            self._notify(f"  ↳ 派出 Worker(#{child_ctx.agent_id}): {task}")

        # Worker 的干净 messages —— 只含 worker 系统提示 + 任务，看不到 coordinator 历史
        sub_messages: list[Message] = [
            Message.system(WORKER_SYSTEM),
            Message.user(task),
        ]

        # 工具白名单 + 防止 worker 再派 worker（worker 不能有 dispatch_worker）
        sub_registry = self._build_registry()
        if self._worker_tools is not None:
            sub_registry = _only_tools(sub_registry, self._worker_tools)
        sub_registry = _without_tool(sub_registry, self.name)

        # ⭐ coordinator 的收件箱 = ctx.mailbox
        # ctx 是 coordinator 的 RunContext；ctx.mailbox 就是它的收件箱。
        # worker 完成后往这里发通知 → coordinator 下一轮能读到。
        leader_mailbox = ctx.mailbox

        # fire-and-forget：后台跑，不阻塞当前 run()
        async def _run_worker() -> None:
            result_msgs = await run_agent_turn(
                sub_messages,
                self._model,
                sub_registry,
                child_ctx,
                max_turns=self._max_turns,
            )
            conclusion = _final_conclusion(result_msgs)
            notification = _format_notification(task, conclusion)
            # ⭐ 结论塞进 leader 的收件箱 —— leader 下一轮会通过 inject_pending_notifications 读到
            leader_mailbox.send(notification)
            if self._notify:
                self._notify(f"  ↳ Worker(#{child_ctx.agent_id}) 完成，通知已投递")

        asyncio.create_task(_run_worker())

        # 立刻返回——不等 worker 跑完（fire-and-forget 的关键）
        return f"Worker dispatched for: {task}"
