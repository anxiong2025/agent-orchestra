# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **learning-oriented** multi-agent orchestration engine that re-implements Claude Code's real
orchestration design from scratch in Python. The goal is not "another framework" but: every module
runs independently, is readable, and teaches one orchestration principle. Built milestone by
milestone (M0–M11), eventually landing on a real business scenario (sourcing/profiling Chinese
suppliers for overseas buyers).

Most docstrings, comments, and design docs are in **Chinese**; model-facing prompts and tool
descriptions are in **English** (deliberate — see the convention below). Match this when editing.

## Commands

All commands run through `uv` — no manual venv activation needed. Python **3.14+** required.

```bash
make install          # uv sync — install deps (incl. dev group)
make run              # uv run orchestra — milestone progress board + what's next
make test             # uv run pytest
make fmt              # uv run ruff format .
make lint             # uv run ruff check .   (includes ASYNC lint rules)
make check            # fmt + lint + test — run this before committing
```

Direct equivalents work too: `uv run orchestra`, `uv run pytest`, `uv run ruff check .`

- Run a single test file: `uv run pytest tests/test_m5_subagent.py`
- Run a single test: `uv run pytest tests/test_m5_subagent.py::test_subagent_runs_and_returns_only_conclusion`
- Interactive chat against real Claude (Bedrock): `uv run orchestra chat`
  Requires AWS env vars (`AWS_BEARER_TOKEN_BEDROCK` / `AWS_REGION`); the SDK reads them — no keys in code.
- `pytest` runs in `asyncio_mode = "auto"`: write `async def test_...` directly, no `@pytest.mark.asyncio` needed.

## Architecture

The whole system is a small set of layers that **compose by reuse, not by adding frameworks**. The
single most important insight: **"multi-agent" is just the main loop calling itself recursively** —
there is no separate orchestration framework.

Data flow, bottom to top:

- **`message.py` (M1)** — `Message` / `ToolCall` / `Role`. Context *is* a list of `Message`. The
  main loop appends to it each turn. A tool result is a `user` message carrying a `tool_call_id`.
- **`model.py` (M1)** — `Model` ABC with one method `complete(messages, tools) -> Message`.
  `MockModel` replays a scripted list of messages, so the entire engine is testable/demoable with no
  API key. Real models implement the same ABC.
- **`providers/` (M1, M9)** — provider implementations of `Model`. `bedrock.py` talks to real Claude
  via AWS Bedrock and owns the **bidirectional tool-call translation** (our unified `tools` schema +
  `tool_result` messages ↔ Anthropic API format). The orchestration core never sees Anthropic's
  field shapes. `make_model("bedrock")` is the factory.
- **`tool.py` (M3)** — `Tool` ABC (`name` / `description` / `input_schema` / `is_concurrency_safe` /
  `async run()`) and `ToolRegistry`. Each tool **self-declares** `is_concurrency_safe` rather than
  the scheduler guessing. Example tools: `ReadFileTool`/`ClockTool` (safe=True), `WriteFileTool`
  (safe=False). Blocking IO is pushed to threads via `asyncio.to_thread`.
- **`context.py` (M3)** — `RunContext`: an `abort` `asyncio.Event` (cooperative cancellation,
  shared across child contexts) and `depth` (recursion level). `ctx.child()` → depth+1, same abort.
- **`orchestration.py` (M4) ⭐** — `partition_tool_calls` slices one turn's tool calls into
  **contiguous batches**: runs of read-only tools become one batch run concurrently (`asyncio`,
  `Semaphore`-capped at `MAX_CONCURRENCY=10`, yields as each completes); any write tool gets its own
  batch and runs serially. Partitioning is *contiguous* (not "all reads together") to preserve
  read→write→read ordering. Completion order ≠ original order is safe because the provider pairs
  results by `tool_call_id`.
- **`loop.py` (M3/M4)** — `run_agent_turn`: the ReAct loop (Reason → Act → Observe). Send history to
  model → if no tool calls, done → else feed all tool calls to `run_tools`, append results, repeat.
  `DEFAULT_MAX_TURNS=10` is the circuit breaker against infinite tool-calling. `run_chat_loop` is the
  REPL shell with injectable I/O.
- **`subagent.py` (M5)** — `AgentTool` *is a tool* (peer to `ReadFileTool`). Its `run()` ends by
  calling `run_agent_turn` again with a **clean, isolated `messages`** (system prompt + the sub-task
  only — never the parent's history) and returns only the **final conclusion**. Because
  `AgentTool.is_concurrency_safe = True`, multiple `spawn_agent` calls in one turn fall into M4's
  read-concurrent batch and **run in parallel for free** — no special multi-agent scheduling code.
- **`coordinator.py` (M6)** / **`mailbox.py` (M7)** — not yet implemented (`raise
  NotImplementedError` with a TODO header). M6 = Orchestrator-Workers (star). M7 = inter-agent
  mailbox (mesh).
- **`cli.py`** — `uv run orchestra` prints the milestone board; `uv run orchestra chat` wires up
  model + tools and hands off to `run_chat_loop`. The `MILESTONES` list here tracks done/not-done.

### Over-spawning defense (M5)

A key design lesson baked into `subagent.py`: preventing runaway sub-agent recursion uses three
layers, in order of importance:

1. **Tool whitelist (root fix)** — sub-agents *don't get the `spawn_agent` tool by default*
   (`allow_nesting=False` strips it from the child registry). No hammer → no over-splitting. This is
   preferred over a depth counter because it prevents nesting at the source. `subagent_tools` further
   narrows the child's tools by task type (e.g. research tasks get read-only tools, no `write_file`).
2. **Prompt discipline** — the sub-agent system prompt says "execute directly, do NOT spawn".
3. **"When NOT to use" guidance** — `spawn_agent`'s description tells the model not to spawn for
   trivial work.

`max_depth` (`DEFAULT_MAX_DEPTH=2`) is only a fallback guard, active when `allow_nesting=True`.

`scripts/verify_m5_no_overspawn.py` and `scripts/diag_m5_timing.py` are one-off scripts that run the
real Bedrock model to check spawn count / depth / timing.

## Conventions

- **Every module maps to a milestone and a Claude Code source file.** Module docstrings start with
  `对标 Claude Code: <path>` (the TS file being re-implemented) and explain the principle taught.
  Preserve these anchors when editing — they are the point of the project.
- **Model-facing text (system prompts, tool `description`, `input_schema` descriptions) is written in
  English**, with Chinese explanation in adjacent comments. Rationale (per the code): models follow
  English instructions — especially negations like "Do NOT…" — more reliably and with fewer tokens.
  Keep new prompts/tool descriptions in English.
- Unimplemented milestone modules contain a top-of-file TODO block and `raise NotImplementedError`.
  To implement one: read its docstring + the referenced spec section, delete the raise, follow the TODO.
- Ruff is intentionally restrained (E, F, I, UP, B, **ASYNC**; line-length handled by formatter).
  This is async-heavy code — heed the ASYNC lint rules.

## Working principles

LLMs make the same predictable mistakes. These are rules, not suggestions — adapted to this repo.

- **Read before you write.** Read the module you're editing *and* its `对标 Claude Code:` docstring
  before changing it. Look at how peers do it (`tool.py` for a new tool, `test_m5_*.py` for expected
  behavior). Check imports — don't introduce a dependency when the stdlib/`asyncio`/`anthropic` is
  already the pattern. If you don't see a pattern, ask rather than guess.
- **Think before you code.** State assumptions and tradeoffs *before* writing. If "add X" is
  ambiguous, say which reading you picked. If two approaches exist, name both with a recommendation —
  not five. When the requirement is confusing, stop and ask; don't fill the gap with plausible code.
- **Simplicity.** Write the minimum that solves *this* problem now. This is a teaching repo: an extra
  abstraction, config knob, or interface-with-one-impl costs reader comprehension for zero benefit.
  No premature abstraction, no speculative error handling, no "in case we need it." Duplicate twice
  before you abstract.
- **Surgical changes.** Keep diffs minimal. Don't touch what you weren't asked to. Match the existing
  style (Chinese comments + English model-facing text, the milestone docstring format). Don't
  reformat, reorder imports, or run a formatter over untouched lines — `make fmt` only as the final
  step. Justify every changed line by the task; revert "while I was in there" edits.
- **Verification.** Run `make test` before and after; report pre-existing failures rather than
  absorbing blame. For a bug, write a `MockModel`/scripted test that reproduces it first, watch it
  fail, then fix. Test behavior (isolation, batching, conclusion-only return), not trivial getters.
  Run `make check` before declaring done. Don't claim it works if you only think it does.
- **Goal-driven.** Make vague tasks verifiable before coding ("add maxTurns guard" → "loop stops at N
  turns, appends a marker message, test asserts both"). For multi-step work, state the plan first.
- **Debug by investigating, not guessing.** Read the whole error + traceback. Reproduce before
  fixing. Change one thing at a time. Find the root cause — don't paper over a `None` with a guard.
  Note: `loop.py:97` has an invalid `except KeyboardInterrupt, EOFError:` (missing parens) — a real
  example of code that looks fine but would `SyntaxError` if reached.
- **Dependencies.** Don't add one without saying why. Prefer stdlib / what's already here
  (`asyncio`, `anthropic[bedrock]`). This project deliberately runs on a tiny dependency set.
- **Communicate.** Say what you changed and why, flag concerns proactively, and be precise about what
  you're *uncertain* of (so the user knows what to verify) vs. what you've confirmed.

## Where to read the design

`specs/` is the full design package (Chinese). Start at `specs/00-README-导读.md`. Most relevant:

- `specs/01-功能需求清单.md` — the 24 features being re-implemented (the definition of "done").
- `specs/05-技术架构设计.md` — layered architecture, module dependencies, data flow.
- `specs/06-多agent编排规格.md` — orchestration specs (recursive / star / mesh).
- `specs/07-迭代开发计划.md` — the M0–M11 milestone breakdown and ordering.

`docs/` holds rendered explainer HTML/Markdown (hooks mechanism, architecture diagrams).
