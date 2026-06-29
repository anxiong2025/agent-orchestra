"""M5 · 子 Agent —— 递归 + 上下文隔离。

对标 Claude Code: src/tools/AgentTool/runAgent.ts(§3),结尾再调 query()
核心顿悟: 子 Agent = 带一份隔离的上下文,再调一次主循环 run_agent_turn(M3/M4)。
          "多 Agent" 不是新框架,而是【主循环的递归复用】。

三个看点:
  1. 递归    —— AgentTool.run() 的结尾就是 await run_agent_turn(...),和主 Agent 跑同一段代码。
  2. 隔离    —— 子 Agent 拿到的是一份【干净 messages】(只含子任务),看不到父级历史;
                只把【最终结论】交回父级 —— 中间探索过程不污染父级上下文/预算。
  3. 防套娃   —— 见下方"三道防线"。

并行白捡: AgentTool.is_concurrency_safe = True → 多个子 Agent 调用直接落入 M4 的
          "只读并发批",自动并行。我们没有为"多 Agent 并行"写任何专门的调度代码。

────────────────────────────────────────────────────────────────────────
防"过度拆分"三道防线(吸取 Claude Code 真实源码的设计):

  ① 工具白名单(治本)—— 子 Agent 默认【拿不到 spawn_agent 工具】。
       对标 constants/tools.ts 的 ALL_AGENT_DISALLOWED_TOOLS:非内部用户的子 Agent
       工具池里直接剔除 Agent 工具。没有"派分身"这把锤子,自然不会到处找钉子。
       —— 这比"深度计数器"更干脆:从源头杜绝套娃,而不是等套了再拦。
  ② Prompt 纪律 —— 子 Agent 系统提示明确写"自己直接干完,别再派子 Agent"。
       对标 forkSubagent.ts 的 "Do NOT spawn sub-agents; execute directly"。
  ③ "何时不该派"指导 —— spawn_agent 的工具描述里列出"简单读文件/查时间别派分身"。
       对标 prompt.ts 的 "When NOT to use the Agent tool"。

  max_depth 保留作为"万一显式开了嵌套(allow_nesting=True)"时的兜底护栏。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from orchestra.context import RunContext
from orchestra.loop import DEFAULT_MAX_TURNS, run_agent_turn
from orchestra.message import Message, Role
from orchestra.model import Model
from orchestra.tool import Tool, ToolRegistry

# 递归深度上限:主 Agent=0,每派一层子 Agent +1。仅在 allow_nesting=True 时作为兜底护栏。
DEFAULT_MAX_DEPTH = 2

# 子 Agent 的系统提示。
# ── 为什么用英文 ──────────────────────────────────────────────────────────
#   面向模型的系统级 prompt 一律用英文(对标 Claude Code 的 DEFAULT_AGENT_PROMPT,
#   src/constants/prompts.ts 全英文):模型对英文指令(尤其 "Do NOT…" 这类否定句)
#   遵循更稳、token 更省。中文写在每行后面的注释里,只为讲清意图,不进 prompt。
# ── 两个要点 ──────────────────────────────────────────────────────────────
#   · 防线②(对标 "execute directly"):压住分身"自己干完,别再派"。
#   · 结论简短(对标 "Keep your report under 500 words"):啰嗦输出几千字会让单次
#     生成飙到 60 秒、还污染父级上下文 —— 强制收口。
SUBAGENT_SYSTEM = (
    "You are a sub-agent dispatched to complete a single, well-scoped task. "  # 你是被派来办一件具体子任务的子 Agent
    "Use the tools available to you and complete the task directly yourself. "  # 用你手上的工具,自己直接干完
    "Do NOT spawn new sub-agents — you are the worker. "  # 别再派新的子 Agent,你就是干活的那个
    "When done, give a concise final result the caller can use directly; "  # 完成后给出可直接使用的简洁结论
    "do not narrate your process. "  # 不要复述过程
    "Keep it under ~200 words — only the essential points the caller needs."  # 控制在 ~200 词内,只给父级需要的关键要点
)


class AgentTool(Tool):
    """把一个子任务派给"分身"独立完成,只回传结论。

    它【本身是一个工具】—— 和 ReadFileTool 平级,模型靠 description 决定何时派分身。
    标记 is_concurrency_safe=True,所以多个子 Agent 调用会落入 M4 的只读并发批 → 自动并行。
    """

    name = "spawn_agent"
    # 面向模型的工具描述 → 英文(对标 Claude Code AgentTool/prompt.ts);中文注释见下。
    # 含一段 "When NOT to use"(对标 prompt.ts:232 的 "When NOT to use the Agent tool"):
    # 简单的事自己用工具更快,别派分身 —— 这是防过度拆分的"防线③"。
    description = (
        # 把一个独立、需要多步探索的子任务派给全新子 Agent;它在隔离上下文里干完,只回传结论。
        "Dispatch an independent, multi-step sub-task to a fresh sub-agent. "
        "It works in an isolated context and returns only the final result. "
        # 适合:能独立完成、互不依赖、适合分头并行的较大任务(同一轮派多个会并行跑)。
        "Best for larger sub-tasks that are self-contained, mutually independent, "
        "and worth running in parallel (multiple calls in one turn run concurrently).\n"
        # 何时【不该】用:简单的事自己做更快 —— 读具体文件用 read_file、查时间用 now、
        # 只碰一两个文件的小事自己用工具做。只有真能拆成几块各自要多步探索的大活才派。
        "When NOT to use: simple work is faster done yourself — read a specific file "
        "with read_file, check time with now, handle one or two files directly. "
        "Only spawn when the task truly splits into independent, multi-step pieces.\n"
        # 参数 task:用清晰完整的自然语言描述这个子任务。
        "Parameter `task`: describe the sub-task in clear, complete natural language."
    )
    # ⭐ 这一个标签 = 多 Agent 并行白捡:多个 spawn_agent 调用落入 M4 只读并发批。
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                # 交给子 Agent 独立完成的子任务(完整自然语言描述)。
                "description": (
                    "The self-contained sub-task for the sub-agent to complete, "
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
        max_depth: int = DEFAULT_MAX_DEPTH,
        allow_nesting: bool = False,
        subagent_tools: list[str] | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        """
        model:          子 Agent 用哪个模型(通常和父级共用同一个,省钱省连接)。
        build_registry: 一个工厂,返回子 Agent 能用的工具集 —— 用工厂而非现成实例,
                        是为了每个子 Agent 拿到独立的一份(也方便 M6 给 worker 收窄权限)。
        max_turns:      子 Agent 自己那个 ReAct 循环的熔断上限。
        max_depth:      递归深度上限。仅在 allow_nesting=True 时作为兜底护栏。
        allow_nesting:  ⭐ 防线①(治本)。默认 False:子 Agent 的工具集里【剔除 spawn_agent】
                        —— 没有"派分身"这把锤子,从源头杜绝过度套娃(对标 Claude Code 的
                        ALL_AGENT_DISALLOWED_TOOLS)。设 True 才允许子 Agent 再派,此时
                        靠 max_depth 兜底防无限递归。
        subagent_tools: ⭐ 工具白名单。子 Agent 只能用名字在这个列表里的工具;None=不限。
                        对标 Claude Code 的 ASYNC_AGENT_ALLOWED_TOOLS:按任务类型收窄工具,
                        别给用不上的(比如纯研究/总结任务不该有 write_file —— 给了模型就手痒
                        反复写文件,把长内容当参数生成,单轮飙到 60 秒还停不下来)。
        notify:         可选,派出/收回子 Agent 时调一下(给 CLI 打进度;测试不传 → 安静)。
        """
        self._model = model
        self._build_registry = build_registry
        self._max_turns = max_turns
        self._max_depth = max_depth
        self._allow_nesting = allow_nesting
        self._subagent_tools = subagent_tools
        self._notify = notify

    async def run(self, tool_input: dict[str, Any], ctx: RunContext) -> str:
        # ── 兜底护栏:仅当显式开了嵌套,才靠 depth 拦无限递归 ──────────────────
        # 默认(allow_nesting=False)根本走不到这里冒套娃 —— 子 Agent 压根没有 spawn_agent。
        child_ctx = ctx.child()  # depth + 1,共享同一个 abort 信号
        if self._allow_nesting and child_ctx.depth > self._max_depth:
            return (
                f"拒绝派发子 Agent:递归深度 {child_ctx.depth} 已超过上限 "
                f"{self._max_depth}(防无限套娃 / 指数级烧钱)。请自己直接完成这个子任务。"
            )

        task = tool_input.get("task", "").strip()
        if not task:
            return "错误:task 不能为空 —— 请描述要派给子 Agent 的子任务。"

        if self._notify:
            self._notify(
                f"  ↳ 派出子 Agent(#{child_ctx.agent_id}←父#{child_ctx.parent_id}, "
                f"depth={child_ctx.depth}): {task}"
            )

        # ── 上下文隔离:一份干净的 messages,只装系统提示 + 子任务 ──────────────
        # 关键:这里【不】把父级的历史抄进来 —— 子 Agent 看不到父级聊了什么,
        # 它在自己的小本子上从零开始,烧自己的 tokens。
        # 注意:child_ctx 不止隔离了 messages —— 它还复制了一份 read_state(文件读缓存),
        #       并带上了独立身份(agent_id/parent_id)。隔离的是【一切可变状态】(见 context.py)。
        sub_messages: list[Message] = [
            Message.system(SUBAGENT_SYSTEM),
            Message.user(task),
        ]
        # ⭐ 工具白名单:先按 subagent_tools 收窄(没传则全保留)。
        sub_registry = self._build_registry()
        if self._subagent_tools is not None:
            sub_registry = _only_tools(sub_registry, self._subagent_tools)
        # ⭐ 防线①:默认从子 Agent 的工具集里剔除 spawn_agent —— 它没法再派分身。
        if not self._allow_nesting:
            sub_registry = _without_tool(sub_registry, self.name)

        # ── 递归:就是再调一次 M3/M4 的主循环。这一行就是"多 Agent"的全部秘密 ──
        result_messages = await run_agent_turn(
            sub_messages,
            self._model,
            sub_registry,
            child_ctx,
            max_turns=self._max_turns,
        )

        # ── 只回传【最终结论】:取最后一条有内容的 assistant 消息 ─────────────
        # 子 Agent 跑出来的一整串历史(系统提示/子任务/各种工具结果)统统丢掉,
        # 父级只拿到这一句结论 —— 这就是"隔离保护父级上下文"的落点。
        conclusion = _final_conclusion(result_messages)
        if self._notify:
            self._notify(f"  ↳ 子 Agent(#{child_ctx.agent_id})交回结论")
        return conclusion


def _without_tool(registry: ToolRegistry, name: str) -> ToolRegistry:
    """返回一个新 registry,剔除指定名字的工具(用于把 spawn_agent 从子 Agent 工具集里拿掉)。

    新建而不原地改:工厂每次造出的是独立一份,删掉自己也不影响别的子 Agent。
    """
    return ToolRegistry([t for t in registry.all() if t.name != name])


def _only_tools(registry: ToolRegistry, allowed: list[str]) -> ToolRegistry:
    """返回一个新 registry,只保留名字在白名单里的工具(对标 ASYNC_AGENT_ALLOWED_TOOLS)。"""
    allow = set(allowed)
    return ToolRegistry([t for t in registry.all() if t.name in allow])


def _final_conclusion(messages: list[Message]) -> str:
    """从子 Agent 跑完的历史里,抽出"最终结论" = 最后一条有文本的 assistant 消息。

    倒着找:跳过结尾可能的工具结果(role=user)和只有工具调用、没正文的 assistant。
    """
    for m in reversed(messages):
        if m.role is Role.assistant and m.content:
            return m.content
    return "(子 Agent 没有产出结论)"
