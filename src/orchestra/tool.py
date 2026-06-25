"""M3 · 工具系统 —— Agent 的"手"。

对标 Claude Code: src/Tool.ts(工具抽象、isConcurrencySafe)
讲清的原理: 工具自己声明能不能并发,而不是让调度器去猜(声明式并发的根)。
            is_concurrency_safe 现在只是个声明字段,M4 的读并发/写独占分批会真正用到它。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from orchestra.context import RunContext


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
    """读文件(只读 → safe=True)。"""

    name = "read_file"
    # 面向模型的描述用英文(中文见注释):读取本地文本文件的内容,参数 path 是文件路径。
    description = "Read the contents of a local text file. Parameter `path` is the file path."
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
                    return f.read()
            except OSError as e:
                return f"读取失败: {e}"

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
