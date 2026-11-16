"""State of the code-agent subgraph.

Shared with HubState: `messages`, `active_project_root`. Everything else is run-scoped and is reset
by the `entry` node, so a new /code request never inherits data from a previous one.
Only JSON-friendly values are stored so any LangGraph checkpointer can persist them.
"""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class CodeState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    active_project_root: str

    request: str            # user request (agent prefix already stripped by CLI/Telegram)
    kind: str               # create | modify | refactor | debug | test
    project_root: str
    settings: dict          # CodeSettings as dict, frozen for this run
    tool_calls: int         # reads, searches, LLM calls and commands spent so far
    repair_attempts: int    # repair rounds used (validation failures and failed checks share it)

    context: str            # text sent to Gemini
    context_files: list[str]
    touched: list[str]      # files already changed in this run (re-read first when repairing)
    feedback: str           # why the previous proposal was rejected / failed
    model_key: str

    proposal: dict          # Proposal.model_dump()
    changes: list[dict]     # validated changes (full new text + diff), not yet applied
    diff_text: str
    warnings: list[str]
    checks_plan: list[list[str]]

    approved: bool
    cancelled: bool
    applied_all: list[dict]     # {path, action, added, removed}, merged across rounds
    backup_dirs: list[str]
    check_results: list[dict]
    round: int              # current work round (1-based)
    passes: int             # propose passes so far (bounded: stays inside LangGraph's recursion limit)
    base_request: str       # what the user originally asked
    round_goal: str         # what this round is for (follow-up rounds)
    read_next: list[str]    # files the review step wants read in the next round
    summary_log: list[dict] # {round, files, summary} per applied round
    next_action: str        # "", "continue" or "ask" (decided by the review step)
    pending_question: str
    stopped: bool           # user ended the run at a follow-up question
    need_repair: bool
    last_errors: list[str]
    notes: list[str]
    done: bool
    error: str
