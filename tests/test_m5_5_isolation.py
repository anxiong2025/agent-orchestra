"""M5.5 测试 —— 完整的上下文隔离:不只隔 messages,还隔【一切可变状态】+ 身份追踪。

承接 M5(test_m5_subagent.py 验证了 messages 隔离)。本文件验证 Claude Code 工业级
稳定的根基那一条:子 Agent fork 时,可变状态(文件读缓存)要【复制隔离】,身份要【独立】。

验证四件事:
  1. read_state 随 child() 复制 —— 子上下文改自己的缓存,父级缓存不受影响。
  2. abort 随 child() 共享     —— 父级一取消,子上下文同一个信号也已 set。
  3. 身份/深度正确派生         —— depth+1、agent_id 各异、parent_id 指向父级。
  4. 端到端:子 Agent 读文件污染的是自己的副本,父级"文件视图"保持干净。
"""

from __future__ import annotations

from pathlib import Path

from orchestra.context import FileMark, ReadFileState, RunContext
from orchestra.loop import run_agent_turn
from orchestra.message import Message, ToolCall
from orchestra.model import MockModel
from orchestra.subagent import AgentTool
from orchestra.tool import ReadFileTool, ToolRegistry

# ── 1. 可变状态(read_state)复制隔离 ─────────────────────────────────────


def test_child_read_state_is_copied_not_shared():
    """child() 给子 Agent 一份独立的读缓存:子改自己的,父级看不到。"""
    parent = RunContext()
    parent.read_state.seen["父读过.txt"] = FileMark(size=10, mtime=1.0)

    child = parent.child()
    # 子继承了父级此刻已读的快照(复制,不是空的)。
    assert "父读过.txt" in child.read_state.seen

    # 子 Agent 读了新文件 —— 只进它自己的副本。
    child.read_state.seen["子读过.txt"] = FileMark(size=20, mtime=2.0)

    # ★ 关键:父级的缓存【没有】被子 Agent 的读取污染。
    assert "子读过.txt" not in parent.read_state.seen
    assert "子读过.txt" in child.read_state.seen
    # 反过来父级再读,也不会漏进子级(两份彻底独立)。
    parent.read_state.seen["父又读.txt"] = FileMark(size=30, mtime=3.0)
    assert "父又读.txt" not in child.read_state.seen


def test_read_file_state_copy_is_independent():
    """ReadFileState.copy() 出来的是独立 dict(改副本不动原件)。"""
    original = ReadFileState()
    original.seen["a"] = FileMark(size=1, mtime=1.0)
    dup = original.copy()
    dup.seen["b"] = FileMark(size=2, mtime=2.0)
    assert "b" not in original.seen
    assert "a" in dup.seen  # 原有内容被带过去了


# ── 2. abort 共享(控制面) ───────────────────────────────────────────────


def test_child_shares_abort_signal():
    """控制面共享:父级 set 了 abort,子上下文看到的是【同一个】已 set 的信号。"""
    parent = RunContext()
    child = parent.child()
    assert child.abort is parent.abort  # 同一个对象
    parent.abort.set()
    assert child.abort.is_set()  # 父取消 → 子也停


# ── 3. 身份 / 深度派生 ────────────────────────────────────────────────────


def test_child_identity_and_depth():
    """每个子上下文:depth+1、独立 agent_id、parent_id 指向父级。"""
    parent = RunContext()
    child = parent.child()
    grandchild = child.child()

    # 深度逐层 +1。
    assert (parent.depth, child.depth, grandchild.depth) == (0, 1, 2)
    # 身份各不相同。
    assert len({parent.agent_id, child.agent_id, grandchild.agent_id}) == 3
    # parent_id 串成一条链:孙→子→父。
    assert child.parent_id == parent.agent_id
    assert grandchild.parent_id == child.agent_id
    # 根上下文没有父亲。
    assert parent.parent_id is None


# ── 4. 端到端:子 Agent 读文件不污染父级"文件视图" ────────────────────────


class _ReadThenConclude(MockModel):
    """子模型:先调 read_file 读指定文件,再给一句结论。"""

    def __init__(self, path: str) -> None:
        super().__init__(
            script=[
                Message.assistant(
                    tool_calls=[
                        ToolCall(id="r1", name="read_file", input={"path": path})
                    ]
                ),
                Message.assistant(content="子结论:读完了"),
            ]
        )


async def test_subagent_file_read_does_not_pollute_parent(tmp_path: Path):
    """端到端:主 Agent 派子 Agent 去读文件;读取记进【子】缓存,父级缓存仍为空。"""
    f = tmp_path / "只给子读.txt"
    f.write_text("子 Agent 专属内容", encoding="utf-8")

    parent_ctx = RunContext()
    sub_model = _ReadThenConclude(str(f))
    agent_tool = AgentTool(sub_model, lambda: ToolRegistry([ReadFileTool()]))

    parent_model = MockModel(
        [
            Message.assistant(
                tool_calls=[
                    ToolCall(id="a1", name="spawn_agent", input={"task": "去读文件"})
                ]
            ),
            Message.assistant(content="父级:收到子结论"),
        ]
    )
    registry = ToolRegistry([agent_tool])
    messages: list[Message] = [Message.user("派人去读那个文件")]

    await run_agent_turn(messages, parent_model, registry, parent_ctx)

    # ★ 子 Agent 读了文件,但污染的是它自己 child() 出来的那份缓存副本 ——
    #   父级上下文的 read_state 始终是空的(它自己没读过任何文件)。
    assert parent_ctx.read_state.seen == {}
