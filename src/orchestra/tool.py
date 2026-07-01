"""M3 · 工具系统 —— Agent 的"手"。

对标 Claude Code: src/Tool.ts(工具抽象、isConcurrencySafe)
讲清的原理: 工具自己声明能不能并发,而不是让调度器去猜(声明式并发的根)。
            is_concurrency_safe 现在只是个声明字段,M4 的读并发/写独占分批会真正用到它。
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from orchestra.context import FileMark, RunContext


class Tool(ABC):
    """一个工具 = 名字 + 描述 + 一段能跑的 run()。模型靠 description 决定要不要用。"""

    name: ClassVar[str]
    description: ClassVar[str]
    # 只读=True(能和别人并发跑) / 写=False(得独占)。M4 分批的依据。
    is_concurrency_safe: ClassVar[bool] = True
    # 入参 schema(JSON Schema),provider 会翻译给模型看。
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    @abstractmethod
    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        """执行工具,返回结果字符串(会被回灌进对话历史)。"""


class ToolRegistry:
    """按名字注册和查找工具。模型说"调 read_file",这里把名字映射到实例。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())


# ── 示例工具 ──────────────────────────────────────────────────────────────


class ReadFileTool(Tool):
    """读文件(只读 → safe=True)。

    M5.5:读取走 ctx.read_state 这份【可变缓存】—— 读完把文件元信息记进缓存。
    这正是"需要被隔离的可变状态":子 Agent 读一堆文件只改它自己那份副本(见 RunContext.child),
    父级的"文件视图"不受影响。对标 Claude Code 的 readFileState。
    """

    name = "read_file"
    # 面向模型的描述用英文(中文见注释):读取本地文本文件的内容,参数 path 是文件路径。
    description = (
        "Read the contents of a local text file. Parameter `path` is the file path."
    )
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        # 文件路径
        "properties": {"path": {"type": "string", "description": "The file path."}},
        "required": ["path"],
    }

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        path = tool_input.get("path", "")

        def _read() -> str:
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                return f"读取失败: {e}"
            # ★ 读成功后,把这个文件记进【本 Agent 自己的】读缓存(可变状态)。
            #   子 Agent 在这里写的是它 child() 时拿到的副本,不会动到父级的缓存。
            st = os.stat(path)
            ctx.read_state.seen[path] = FileMark(size=st.st_size, mtime=st.st_mtime)
            return content

        # 文件 IO 是阻塞的,丢到线程里,别卡事件循环(为 M4 并发铺路)。
        return await asyncio.to_thread(_read)


class ClockTool(Tool):
    """报当前时间(只读 → safe=True)。"""

    name = "now"
    # 返回服务器当前的日期和时间。无参数。
    description = "Return the server's current date and time. No parameters."
    is_concurrency_safe = True

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        from datetime import datetime

        return datetime.now().isoformat(timespec="seconds")


class SendMessageTool(Tool):
    """给指定 agent_id 发一条消息(网状通信,M7)——对标 tools/SendMessageTool/。

    只读=True:发消息只是往【另一个 Agent 自己的】mailbox 里塞一条,不碰共享可变状态
    (M4 的读并发/写独占分类里,这和 read_file 是一类:各发各的,互不冲突)。

    寻址靠 ctx.directory(见 context.py)—— 花名册记录"agent_id → 它的 mailbox",
    每个 RunContext 创建时自动登记自己。对方是否收到,取决于它的循环下一轮
    是否调了 inject_pending_notifications()(M6 已接好)去 poll() 自己的 mailbox,
    或是它正在 await mailbox.receive() 挂起等待。
    """

    name = "send_message"
    description = (
        # 直接给另一个 agent(按数字 id)发一条消息;对方在自己下一轮循环里会看到。
        "Send a message directly to another agent, addressed by its numeric agent id. "
        "The message lands in that agent's mailbox and it will see it before its next turn.\n"
        # 用于对等协作(不是上下级派活/回灌那种单向关系)——比如请同事帮查一件事,或回复收到的消息。
        "Use this for peer-to-peer coordination between agents that are not in a "
        "leader/worker relationship — e.g. asking a peer to check something, or replying "
        "to a message you received.\n"
        # 不该用:回复自己的派发者/调用者——那是通过返回值自动完成的,不用这个工具。
        "When NOT to use: replying to your own dispatcher/caller — that happens "
        "automatically via your return value, not this tool.\n"
        "Parameter `to`: the numeric agent id of the recipient. "
        "Parameter `message`: the message content, in complete natural language."
    )
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            # 收件人的数字 agent id / 要发送的消息内容
            "to": {
                "type": "integer",
                "description": "The recipient agent's numeric id.",
            },
            "message": {
                "type": "string",
                "description": "The message content to send.",
            },
        },
        "required": ["to", "message"],
    }

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        raw_to = tool_input.get("to")
        try:
            to = int(raw_to)  # 模型可能把数字 id 传成字符串,兼容一下
        except TypeError, ValueError:
            return f"Error: `to` must be a numeric agent id, got {raw_to!r}."

        message = tool_input.get("message", "").strip()
        if not message:
            return "Error: message cannot be empty."

        target = ctx.directory.get(to)
        if target is None:
            return f"Error: no agent with id {to} (it may not exist, or has already finished)."

        # 包一层 <message from="..."> ,对方模型能知道回复给谁(填进下一次 send_message 的 to)。
        target.send(f'<message from="{ctx.agent_id}">{message}</message>')
        return f"Message sent to agent {to}."


class WriteFileTool(Tool):
    """写文件(改磁盘 → safe=False,必须独占一批,不能和别人并发)。"""

    name = "write_file"
    # 把文本写入本地文件。参数 path(路径)和 content(内容)。
    description = (
        "Write text to a local file. "
        "Parameters: `path` (file path) and `content` (text to write)."
    )
    # ⭐ M4 的关键:声明自己不可并发 → partition 会让它独占一批、串行跑。
    is_concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            # 文件路径 / 要写入的内容
            "path": {"type": "string", "description": "The file path."},
            "content": {"type": "string", "description": "The text to write."},
        },
        "required": ["path", "content"],
    }

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")

        def _write() -> str:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"已写入 {len(content)} 字符到 {path}"
            except OSError as e:
                return f"写入失败: {e}"

        return await asyncio.to_thread(_write)
