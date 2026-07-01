"""M3 · 运行上下文 —— 贯穿一次运行的状态 + 取消信号 + 递归深度 + 身份 + 可变状态隔离。

对标 Claude Code: src/Tool.ts 的 ToolUseContext;abort 信号见 runAgent.ts §3.3;
                  子 Agent 的"状态隔离/身份"见 runAgent.ts §3.1(fork child 复制 readFileState、
                  分配独立 agentId、深度 +1)。
讲清的原理:
  1. 取消信号(abort)沿上下文传播 —— 控制面【共享】:父级一取消,所有子 Agent 跟着停。
  2. 可变状态(read_state)随 child() 【复制隔离】—— 数据面【不共享】:子 Agent 怎么折腾
     自己的"文件视图"都不会改到父级的。这是 Claude Code 工业级稳定的根基:
     ★ 上下文隔离不是只隔对话历史(messages),而是【隔离一切可变状态】。
  3. 身份(agent_id / parent_id) + 深度(depth)—— 让系统随时知道"我是谁、爹是谁、嵌套第几层",
     服务于日志追踪、M6 worker 区分、M7 mailbox 寻址。

为什么没有"关闭全局 UI 状态写入"那一条(对标 CC 把子 Agent 的 setState 改成空操作)?
  —— 那是 Claude Code 作为 React TUI/IDE 才有的东西(主线程用 store 管全局 UI 状态,
     异步子 Agent 若也能写就会和主线程抢同一份状态、界面错乱)。本仓库是无 UI 的库,
     【根本不存在全局 UI store】,所以这条对我们是 N/A —— 不是简化原理,而是"被隔离物不存在"。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count

from orchestra.mailbox import Mailbox

# 进程内自增的 agent 序号 —— 给每个 RunContext 一个稳定可读的身份,便于日志/追踪。
# (不用随机 UUID:可读性更好,且测试可预期。)
_agent_seq = count()


@dataclass(frozen=True)
class FileMark:
    """一个文件被读过时的元信息快照(不可变 → 可安全在副本间共享)。"""

    size: int
    mtime: float


@dataclass
class ReadFileState:
    """文件读缓存 —— 一份【可变状态】,记录"哪些文件读过、当时的大小/修改时间"。

    对标 Claude Code 的 readFileState(runAgent.ts)。它的工程价值有二:
      · 性能:重复读同一文件可走缓存,不必每次真读磁盘。
      · 一致性:Agent 能感知"我读过这个文件了"(避免对同一文件重复 IO/重复推理)。

    ★ 正因为它是【可变】的,父子共享就会互相污染:子 Agent 读了文件 A,父级缓存也被改,
      父级会误以为"我也读过 A"。所以 child() 必须给子 Agent 一份【独立副本】。
    """

    # path → 该文件最近一次被读到时的元信息(大小、修改时间)。
    seen: dict[str, FileMark] = field(default_factory=dict)

    def copy(self) -> ReadFileState:
        """浅拷贝出一份独立缓存(dict 重建,FileMark 不可变可共享)——给 fork 出的子 Agent 用。"""
        return ReadFileState(seen=dict(self.seen))


@dataclass
class RunContext:
    """一次运行的上下文。工具拿到它,能感知"是否该取消""我是谁/几层""文件读过没"。"""

    # ── 控制面:共享 ──────────────────────────────────────────────────────
    # 取消信号:谁都能 .set() 它,工具/循环看到 is_set() 就该停。child() 时【共享同一个】,
    # 所以父级一取消,所有子 Agent 一起停。
    abort: asyncio.Event = field(default_factory=asyncio.Event)

    # ── 数据面:隔离 ──────────────────────────────────────────────────────
    # 文件读缓存:可变状态。child() 时【复制一份】给子 Agent,互不污染。
    read_state: ReadFileState = field(default_factory=ReadFileState)

    # ── 通信面:每个 Agent 各自独立 ───────────────────────────────────────
    # 自己的收件箱：别人往这里 send()，自己 poll()/receive() 来取。
    # child() 时【新建】—— 每个 Agent 有独立收件箱，互不干扰。
    mailbox: Mailbox = field(default_factory=Mailbox)
    # 父级的收件箱引用：子 Agent 跑完后往这里 send() 通知父级。
    # child() 时【传入父级的 mailbox】—— 这是子→父单向通知的通路。
    # 主 Agent(depth=0) 没有父级，为 None。
    leader_mailbox: Mailbox | None = None

    # ── 身份 / 深度:每个 Agent 各异 ──────────────────────────────────────
    # 递归深度:主 Agent=0,每派一层子 Agent +1。M5 用它做"套娃守卫"。
    depth: int = 0
    # 本 Agent 的身份;派生出的子 Agent 会记下父级是谁。用于日志/追踪、M6/M7 寻址。
    agent_id: int = field(default_factory=lambda: next(_agent_seq))
    parent_id: int | None = None

    def child(self) -> RunContext:
        """派生子上下文 —— M5 子 Agent 用。

        三件事一次做齐(这就是"完整隔离"):
          · abort     【共享】—— 控制面:父级取消能波及子 Agent。
          · read_state【复制】—— 数据面:子 Agent 拿独立的文件视图,不污染父级。
          · 身份/深度  【新生】—— depth+1、分配新 agent_id、记住 parent_id。
        """
        return RunContext(
            abort=self.abort,                  # 共享:控制信号
            read_state=self.read_state.copy(), # 复制:可变状态隔离
            mailbox=Mailbox(),                 # 新建:子 Agent 自己的收件箱
            leader_mailbox=self.mailbox,       # 传入:父级收件箱，子完成后通知用
            depth=self.depth + 1,              # 深度 +1
            parent_id=self.agent_id,           # 记住爹是谁
        )
