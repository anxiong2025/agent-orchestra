"""M4 测试 —— 读并发 / 写独占 + 流式。全程 MockModel/假工具,不烧 API。

验证三件事:
  1. partition_tool_calls 按 is_concurrency_safe 切连续批:[R,R,W,R] -> [[R,R],[W],[R]]
  2. 只读批【真的并发】—— 用 sleep + 时间戳证明它们同时开始,而不是排队
  3. 写工具【独占串行】—— 写批之间不重叠
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from orchestra.context import RunContext
from orchestra.message import ToolCall
from orchestra.orchestration import partition_tool_calls, run_tools
from orchestra.tool import Tool, ToolRegistry

# ── 假工具:记录自己的"开始/结束"时间戳,好观察并发 ──────────────────────


class SlowReadTool(Tool):
    """只读 → safe=True。睡一会儿,记下进出时间。"""

    name = "slow_read"
    description = "测试用:睡 0.1s 的只读工具。"
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    def __init__(self) -> None:
        self.events: list[tuple[str, str, float]] = []  # (id, "start"/"end", t)

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        tid = tool_input.get("id", "?")
        self.events.append((tid, "start", time.monotonic()))
        await asyncio.sleep(0.1)
        self.events.append((tid, "end", time.monotonic()))
        return f"read:{tid}"


class SlowWriteTool(Tool):
    """写 → safe=False。同样记时间,用来验证写批不重叠。"""

    name = "slow_write"
    description = "测试用:睡 0.1s 的写工具(独占)。"
    is_concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    def __init__(self) -> None:
        self.events: list[tuple[str, str, float]] = []

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        tid = tool_input.get("id", "?")
        self.events.append((tid, "start", time.monotonic()))
        await asyncio.sleep(0.1)
        self.events.append((tid, "end", time.monotonic()))
        return f"write:{tid}"


def _call(name: str, tid: str) -> ToolCall:
    return ToolCall(id=tid, name=name, input={"id": tid})


# ── 1. 分批规则 ──────────────────────────────────────────────────────────


def test_partition_splits_into_consecutive_batches():
    """[R, R, W, R] 应切成 [[R,R], [W], [R]] —— 写工具打断只读批。"""
    registry = ToolRegistry([SlowReadTool(), SlowWriteTool()])
    calls = [
        _call("slow_read", "r1"),
        _call("slow_read", "r2"),
        _call("slow_write", "w1"),
        _call("slow_read", "r3"),
    ]

    batches = partition_tool_calls(calls, registry)

    assert [[c.id for c in b] for b in batches] == [["r1", "r2"], ["w1"], ["r3"]]


def test_partition_unknown_tool_is_exclusive():
    """未知工具按"不安全"处理 —— 独占一批,不会被塞进只读并发批。"""
    registry = ToolRegistry([SlowReadTool()])
    calls = [_call("slow_read", "r1"), _call("ghost", "g1")]

    batches = partition_tool_calls(calls, registry)

    assert [[c.id for c in b] for b in batches] == [["r1"], ["g1"]]


# ── 2. 只读批真并发 ──────────────────────────────────────────────────────


async def test_reads_run_concurrently():
    """三个只读工具应【同时开始】(并发),而不是一个接一个(串行)。"""
    read = SlowReadTool()
    registry = ToolRegistry([read])
    calls = [_call("slow_read", f"r{i}") for i in range(3)]

    results = [m async for m in run_tools(calls, registry, RunContext())]

    assert len(results) == 3
    # 三个 start 几乎同时:最早和最晚的 start 间隔 << 单个工具的 0.1s。
    starts = [t for _, kind, t in read.events if kind == "start"]
    assert max(starts) - min(starts) < 0.05  # 并发的话三个 start 挤在一起
    # 反证:若串行,3 个 0.1s 工具的 start 会拉开到 ~0.2s。


# ── 3. 写工具独占串行 ────────────────────────────────────────────────────


async def test_writes_run_exclusively():
    """两个写工具各自独占一批 —— 区间不重叠(一个 end 后另一个才 start)。"""
    write = SlowWriteTool()
    registry = ToolRegistry([write])
    calls = [_call("slow_write", "w1"), _call("slow_write", "w2")]

    await _drain(run_tools(calls, registry, RunContext()))

    # 按开始时间排序后,前一个的 end 不晚于后一个的 start(无重叠)。
    spans = _spans(write.events)
    spans.sort(key=lambda s: s[1])  # 按 start 排
    (_, _, end0), (_, start1, _) = spans
    assert end0 <= start1 + 1e-6


async def test_mixed_batch_order_preserved_and_writes_isolated():
    """[R,R,W,R]:两个读并发,写独占,最后那个读在写之后跑。"""
    read, write = SlowReadTool(), SlowWriteTool()
    registry = ToolRegistry([read, write])
    calls = [
        _call("slow_read", "r1"),
        _call("slow_read", "r2"),
        _call("slow_write", "w1"),
        _call("slow_read", "r3"),
    ]

    results = [m async for m in run_tools(calls, registry, RunContext())]

    # 4 个结果都在,且都能按 tool_call_id 配回(回传顺序无所谓)。
    assert {m.tool_call_id for m in results} == {"r1", "r2", "r3", "w1"}
    # 写的开始时间晚于前两个读的开始(写批在只读批之后)。
    w_start = next(t for tid, k, t in write.events if tid == "w1" and k == "start")
    r12_starts = [
        t for tid, k, t in read.events if tid in ("r1", "r2") and k == "start"
    ]
    assert w_start >= max(r12_starts)


# ── 小工具 ───────────────────────────────────────────────────────────────


def _spans(events: list[tuple[str, str, float]]) -> list[tuple[str, float, float]]:
    """把 (id, start/end, t) 事件流折叠成 (id, start, end)。"""
    starts = {tid: t for tid, k, t in events if k == "start"}
    ends = {tid: t for tid, k, t in events if k == "end"}
    return [(tid, starts[tid], ends[tid]) for tid in starts]


async def _drain(agen) -> None:
    async for _ in agen:
        pass
