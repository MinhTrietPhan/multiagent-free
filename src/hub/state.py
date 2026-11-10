"""State of the top-level hub graph. Agents read `messages` and append their reply to it."""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class HubState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    next: str   # agent chosen by the supervisor
    forced: str | None  # <-- thêm: agent do người dùng chọn thẳng