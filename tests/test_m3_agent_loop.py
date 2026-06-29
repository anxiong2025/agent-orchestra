"""M3 测试 —— ReAct 循环(推理→行动→观察)+ maxTurns 熔断,全程用 MockModel,不烧 API。"""

from __future__ import annotations

from typing import Any

from orchestra.context import RunContext
from orchestra.loop import run_agent_turn
from orchestra.message import Message, ToolCall
from orchestra.model import MockModel
from orchestra.tool import Tool, ToolRegistry


class EchoTool(Tool):
    name = "echo"
    description = "原样返回输入的 text。"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        return f"echo: {tool_input.get('text', '')}"


async def test_think_act_see_completes():
    """脚本化:先调工具 → 看到结果 → 再给最终答案,能完整跑完。"""
    script = [
        # 第1轮:模型决定调 echo 工具
        Message.assistant(
            tool_calls=[ToolCall(id="t1", name="echo", input={"text": "hi"})]
        ),
        # 第2轮:看到工具结果后,给纯文本答案(无工具调用 → 结束)
        Message.assistant(content="工具说了:echo: hi"),
    ]
    model = MockModel(script)
    registry = ToolRegistry([EchoTool()])
    messages: list[Message] = [Message.user("帮我 echo hi")]

    result = await run_agent_turn(messages, model, registry, RunContext())

    # 历史应包含:user → assistant(tool_call) → tool_result → assistant(答案)
    assert len(result) == 4
    assert result[1].tool_calls[0].name == "echo"
    assert result[2].tool_call_id == "t1"
    assert result[2].content == "echo: hi"
    assert result[3].content == "工具说了:echo: hi"
    assert not result[3].tool_calls  # 最后一条没有工具调用 = 自然结束


async def test_max_turns_circuit_breaker():
    """模型永远调工具 → maxTurns 必须刹住,不会无限循环。"""

    class AlwaysCallsTool(MockModel):
        async def complete(self, messages, tools=None):
            return Message.assistant(
                tool_calls=[ToolCall(id="x", name="echo", input={"text": "loop"})]
            )

    model = AlwaysCallsTool(script=[])
    registry = ToolRegistry([EchoTool()])
    messages: list[Message] = [Message.user("开始")]

    result = await run_agent_turn(messages, model, registry, RunContext(), max_turns=3)

    # 最后一条应是熔断提示,而不是死循环。
    assert "最大轮数" in result[-1].content
