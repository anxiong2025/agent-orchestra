"""M5 测试 —— 子 Agent:递归 + 上下文隔离 + 深度守卫 + 并行。

全程 MockModel/假工具,不烧 API。验证四件事:
  1. 递归 + 只回传结论 —— 主 Agent 派子 Agent,子 Agent 独立跑完,父级只拿到那一句结论。
  2. 上下文隔离        —— 子 Agent 看不到父级历史(它的 messages 从干净的系统提示+子任务起步)。
  3. 深度守卫          —— depth 超上限时拒绝再派,不会无限套娃。
  4. 并行白捡          —— 同一轮里两个 spawn_agent 调用落入 M4 只读并发批,【同时开始】。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from orchestra.context import RunContext
from orchestra.loop import run_agent_turn
from orchestra.message import Message, Role, ToolCall
from orchestra.model import MockModel
from orchestra.subagent import AgentTool
from orchestra.tool import (
    ClockTool,
    ReadFileTool,
    Tool,
    ToolRegistry,
    WriteFileTool,
)


class ScriptedModel(MockModel):
    """记录每次 complete 收到的 messages,好检查子 Agent 看到的上下文。"""

    def __init__(self, script: list[Message]) -> None:
        super().__init__(script)
        self.seen: list[list[Message]] = []

    async def complete(self, messages, tools=None):
        self.seen.append(list(messages))
        return await super().complete(messages, tools)


# ── 1 + 2. 递归 / 隔离 / 只回传结论 ───────────────────────────────────────


async def test_subagent_runs_and_returns_only_conclusion():
    """主 Agent 派一个子 Agent;子 Agent 直接给结论;父级历史里只出现这句结论。"""
    # 父模型:第1轮决定派子 Agent;第2轮看到结论后收尾。
    parent_model = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(id="a1", name="spawn_agent", input={"task": "查一下 A"})
                ]
            ),
            Message.assistant(content="父级综合答案:A 已查清。"),
        ]
    )
    # 子模型:独立的脚本,一句话给结论(不调工具 → 子循环自然结束)。
    sub_model = MockModel([Message.assistant(content="子 Agent 结论:A 是个好东西。")])

    agent_tool = AgentTool(sub_model, lambda: ToolRegistry())
    registry = ToolRegistry([agent_tool])
    messages: list[Message] = [Message.user("帮我研究 A")]

    result = await run_agent_turn(messages, parent_model, registry, RunContext())

    # 父级历史:user → assistant(派活) → tool_result(子结论) → assistant(综合)
    assert len(result) == 4
    assert result[1].tool_calls[0].name == "spawn_agent"
    # 回灌给父级的工具结果 = 子 Agent 的【最终结论】,而不是它的中间过程。
    assert result[2].role is Role.user and result[2].tool_call_id == "a1"
    assert result[2].content == "子 Agent 结论:A 是个好东西。"
    assert result[3].content == "父级综合答案:A 已查清。"


async def test_subagent_context_is_isolated():
    """子 Agent 看不到父级历史 —— 它的上下文从干净的系统提示 + 子任务起步。"""
    parent_model = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(
                        id="a1", name="spawn_agent", input={"task": "只属于子的任务"}
                    )
                ]
            ),
            Message.assistant(content="done"),
        ]
    )
    sub_model = ScriptedModel([Message.assistant(content="子结论")])

    agent_tool = AgentTool(sub_model, lambda: ToolRegistry())
    registry = ToolRegistry([agent_tool])
    # 父级历史里有一句"秘密",子 Agent 不该看到。
    messages: list[Message] = [Message.user("父级机密:芝麻开门")]

    await run_agent_turn(messages, parent_model, registry, RunContext())

    # 子模型第一次被调用时看到的 messages:只有系统提示 + 子任务,没有父级机密。
    first_seen = sub_model.seen[0]
    assert [m.role for m in first_seen] == [Role.system, Role.user]
    assert first_seen[1].content == "只属于子的任务"
    assert all("芝麻开门" not in m.content for m in first_seen)


# ── 3. 防套娃:三道防线 ───────────────────────────────────────────────────


async def test_subagent_cannot_spawn_by_default():
    """防线①(治本):默认 allow_nesting=False → 子 Agent 工具集里【没有 spawn_agent】。

    子 Agent 看到的工具清单里不含派分身的工具 —— 从源头杜绝套娃,根本不依赖深度计数。
    """
    seen_tools: list[list[str]] = []

    class ToolSpyModel(MockModel):
        async def complete(self, messages, tools=None):
            seen_tools.append([t["name"] for t in (tools or [])])
            return Message.assistant(content="子结论")

    # 子 Agent 的基础工具集里【本来放了】spawn_agent,看它会不会被剔除。
    agent_tool = AgentTool(ToolSpyModel(script=[]), lambda: None)

    def build_with_self() -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(agent_tool)  # 故意把 spawn_agent 放进子 Agent 的工具集
        return reg

    agent_tool._build_registry = build_with_self  # type: ignore[attr-defined]

    await agent_tool.run({"task": "子任务"}, RunContext())

    # 子 Agent 被调用时,工具清单里不该出现 spawn_agent(已被防线①剔除)。
    assert seen_tools and "spawn_agent" not in seen_tools[0]


async def test_subagent_tools_whitelist():
    """工具白名单:子 Agent 只看到 subagent_tools 里列出的工具,别的(如 write_file)被剔除。

    这是修"子 Agent 乱调 write_file 反复写文件、单轮 60 秒"那个 bug 的核心。
    """
    seen_tools: list[list[str]] = []

    class ToolSpyModel(MockModel):
        async def complete(self, messages, tools=None):
            seen_tools.append([t["name"] for t in (tools or [])])
            return Message.assistant(content="子结论")

    # 子 Agent 的工具集里有读/写/时间,但白名单只放 read_file + now。
    def build_full() -> ToolRegistry:
        return ToolRegistry([ReadFileTool(), ClockTool(), WriteFileTool()])

    agent_tool = AgentTool(
        ToolSpyModel(script=[]), build_full, subagent_tools=["read_file", "now"]
    )

    await agent_tool.run({"task": "纯研究任务"}, RunContext())

    assert seen_tools  # 子 Agent 被调用了
    # 只剩白名单里的两个,write_file 被挡在外面。
    assert set(seen_tools[0]) == {"read_file", "now"}
    assert "write_file" not in seen_tools[0]


async def test_depth_guard_refuses_when_nesting_enabled():
    """兜底护栏:显式 allow_nesting=True 时,depth 超上限才拒绝(子模型不被调用)。"""
    sub_model = ScriptedModel([Message.assistant(content="本不该跑")])
    agent_tool = AgentTool(
        sub_model, lambda: ToolRegistry(), max_depth=2, allow_nesting=True
    )

    # 模拟"已经在 depth=2 的子 Agent 里,还想再派":child() 后 depth=3 > 2 → 拒绝。
    out = await agent_tool.run({"task": "再派一个"}, RunContext(depth=2))

    assert "拒绝派发" in out
    assert sub_model.seen == []  # 守卫触发,子模型一次都没被调用


async def test_depth_guard_allows_within_limit():
    """深度没超时(allow_nesting=True),正常派发并跑出结论。"""
    sub_model = MockModel([Message.assistant(content="允许:结论 OK")])
    agent_tool = AgentTool(
        sub_model, lambda: ToolRegistry(), max_depth=2, allow_nesting=True
    )

    out = await agent_tool.run({"task": "正常子任务"}, RunContext(depth=0))

    assert out == "允许:结论 OK"


# ── 4. 并行白捡:两个 spawn_agent 同时开始 ────────────────────────────────


class SlowMarkModel(MockModel):
    """子模型:complete 时睡 0.1s 并记下开始时间,用来观察多个子 Agent 是否并发。"""

    def __init__(self, starts: list[float]) -> None:
        super().__init__(script=[])
        self._starts = starts

    async def complete(self, messages, tools=None):
        self._starts.append(time.monotonic())
        await asyncio.sleep(0.1)
        return Message.assistant(content="慢结论")


async def test_two_subagents_run_concurrently():
    """同一轮里派两个子 Agent → 落入只读并发批 → 两个子模型几乎【同时】开始。"""
    starts: list[float] = []
    agent_tool = AgentTool(SlowMarkModel(starts), lambda: ToolRegistry())
    # 父模型一轮里吐出两个 spawn_agent 调用,然后收尾。
    parent_model = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(id="a1", name="spawn_agent", input={"task": "子任务1"}),
                    ToolCall(id="a2", name="spawn_agent", input={"task": "子任务2"}),
                ]
            ),
            Message.assistant(content="两个都回来了"),
        ]
    )
    registry = ToolRegistry([agent_tool])
    messages: list[Message] = [Message.user("分头去办两件事")]

    await run_agent_turn(messages, parent_model, registry, RunContext())

    assert len(starts) == 2
    # 并发:两个子 Agent 的 start 挤在一起(间隔 << 单个 0.1s);串行的话会差 ~0.1s。
    assert max(starts) - min(starts) < 0.05


# ── 子 Agent 自己也能用工具(递归里照样是完整 ReAct 循环) ─────────────────


class EchoTool(Tool):
    name = "echo"
    description = "原样返回 text。"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        return f"echo: {tool_input.get('text', '')}"


async def test_subagent_can_use_its_own_tools():
    """子 Agent 在自己的隔离循环里照样能调工具(它跑的就是完整的 run_agent_turn)。"""
    # 子模型:先调 echo,再基于结果给结论。
    sub_model = MockModel(
        [
            Message.assistant(
                tool_calls=[ToolCall(id="e1", name="echo", input={"text": "hi"})]
            ),
            Message.assistant(content="子结论:工具说了 echo: hi"),
        ]
    )
    agent_tool = AgentTool(sub_model, lambda: ToolRegistry([EchoTool()]))

    out = await agent_tool.run({"task": "用 echo 工具"}, RunContext())

    assert out == "子结论:工具说了 echo: hi"
