import asyncio
import os
from typing import Annotated, TypedDict
from dotenv import load_dotenv
from fastmcp import Client
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from mcp_tools import format_rls_status, get_rls_status, mcp

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_TURNS = 15
# +4 for the rls_check and planner steps and their edges into the next node;
# the rest covers the agent/tools ping-pong, mirroring the old MAX_TURNS-bounded loop.
RECURSION_LIMIT = MAX_TURNS * 2 + 4
# Tool output is sent to the browser verbatim; outlier lists can be huge, so cap it.
MAX_TOOL_OUTPUT_CHARS = 4000

SYSTEM_INSTRUCTION = (
    "You are a data-quality agent for a Postgres database. You have tools to list "
    "tables, compute column statistics, and detect outliers or unusual row-count "
    "trends, and observe changes in schema. Investigate the schema for data anomalies, then summarize what you "
    "found in plain English. If nothing looks anomalous, say so."
    "For every tool you use, include a short summary of why you are using it and what you are looking for."
    "IF applicable, attempt to link anomalies to recent schema changes. If you find anomalies, suggest a course of action to fix them."
    "Finally, list all anomalous points from each table, and recommend to enable RLS for all tables without it, and to add a column-level audit log for all tables with sensitive data."
)

# A separate step, run before any tool is called, that has the same underlying model
# reason about *strategy* -- which tables and checks are worth investigating and in what
# order -- instead of letting the very first tool call be a blind guess. The plan is
# advisory: it is handed to the acting step as context, not enforced turn-by-turn.
PLANNER_INSTRUCTION = (
    "You are the planning step of a data-quality agent. You will not call any tools "
    "yourself -- another step, using the same model, will do the actual investigation. "
    "Given the task and the catalog of available tools below, write a short, ordered "
    "investigation strategy: which table(s) to look at first, which checks (freshness, "
    "row-count trend, outlier detection via IQR/z-score, schema-change correlation, etc.) "
    "are worth running and in what order, and anything that looks safe to skip. "
    "Keep it to at most 6 bullet points and be concrete (name tables/columns once you know "
    "them from the task). The acting step may deviate from this plan if what it finds "
    "warrants it."
)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    plan: str
    rls: list[dict]  # [{"name": table, "rls": enabled}], set by the rls_check node


def _format_tool_catalog(tools: list[BaseTool]) -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in tools)


def _build_graph(tools: list[BaseTool]):
    # Same base model (Gemini via GEMINI_MODEL) for both steps -- only the temperature and
    # tool-binding differ between the strategic planning step and the tool-calling step.
    planner_llm = ChatGoogleGenerativeAI(model=MODEL, google_api_key=GEMINI_API_KEY)
    acting_llm = ChatGoogleGenerativeAI(model=MODEL, google_api_key=GEMINI_API_KEY).bind_tools(tools)

    async def plan_node(state: AgentState) -> dict:
        task_message = state["messages"][-1]
        prompt = [
            SystemMessage(content=PLANNER_INSTRUCTION),
            HumanMessage(
                content=(
                    f"Task: {task_message.content}\n\n"
                    f"Available tools:\n{_format_tool_catalog(tools)}"
                )
            ),
        ]
        response = await planner_llm.ainvoke(prompt)
        plan = response.text
        print(f"[plan]\n{plan}\n")
        return {
            "plan": plan,
            "messages": [SystemMessage(content=f"Investigation plan from the planning step:\n{plan}")],
        }

    async def rls_check_node(state: AgentState) -> dict:
        # Deterministic pre-check, run once before any tool-calling turn -- not something
        # the LLM chooses to run, so it can't be skipped or forgotten.
        rows, is_mock = await asyncio.to_thread(get_rls_status)
        result = format_rls_status(rows, is_mock)
        print(f"[rls check]\n{result}\n")
        return {
            "rls": [{"name": table, "rls": enabled} for table, enabled in rows],
            "messages": [
                SystemMessage(content=f"Automated RLS check (ran before investigation, not model-invoked):\n{result}")
            ]
        }

    async def agent_node(state: AgentState) -> dict:
        response = await acting_llm.ainvoke(state["messages"])
        for call in response.tool_calls:
            print(f"[tool call] {call['name']}({call['args']})")
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("rls_check", rls_check_node)
    graph.add_node("planner", plan_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "rls_check")
    graph.add_edge("rls_check", "planner")
    graph.add_edge("planner", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()


def _initial_state(task: str) -> AgentState:
    return {
        "messages": [SystemMessage(content=SYSTEM_INSTRUCTION), HumanMessage(content=task)],
        "plan": "",
        "rls": [],
    }


async def run_agent(task: str) -> str:
    async with Client(mcp) as mcp_client:
        tools = await load_mcp_tools(mcp_client.session)
        app = _build_graph(tools)

        final_state = await app.ainvoke(
            _initial_state(task),
            config={"recursion_limit": RECURSION_LIMIT},
        )

        final_message = final_state["messages"][-1]
        if isinstance(final_message, AIMessage) and not final_message.tool_calls:
            return final_message.text
        return "Stopped after max turns without a final answer."


def _content_text(content) -> str:
    """Tool message content is either a plain string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more characters truncated]"


def _events_for(node: str, delta: dict):
    """Translate one finished graph node into zero or more (event_name, payload) pairs.

    Event names and payload shapes are the contract documented at the top of frontend/app.js.
    """
    if node == "rls_check":
        yield "rls", {"tables": delta.get("rls", [])}
    elif node == "planner":
        yield "plan", {"text": delta.get("plan", "")}
    elif node == "agent":
        for message in delta.get("messages", []):
            if message.tool_calls:
                for call in message.tool_calls:
                    yield "tool_call", {"id": call["id"], "name": call["name"], "args": call["args"]}
            else:
                # No tool calls means the model is done: this message is the final report.
                yield "summary", {"text": message.text}
    elif node == "tools":
        for message in delta.get("messages", []):
            yield "tool_result", {
                "id": message.tool_call_id,
                "output": _truncate(_content_text(message.content)),
            }


async def stream_agent(task: str):
    """Run the agent, yielding (event_name, payload) pairs as each graph step finishes.

    The last pair is always either ("done", {}) or ("error", {...}). Unexpected exceptions
    are not caught here; the caller (the route) decides how to report them.
    """
    if not GEMINI_API_KEY:
        yield "error", {"message": "GEMINI_API_KEY is not configured on the server."}
        return

    try:
        async with Client(mcp) as mcp_client:
            tools = await load_mcp_tools(mcp_client.session)
            app = _build_graph(tools)
            # stream_mode="updates" yields {node_name: state_changes} each time a node finishes.
            async for update in app.astream(
                _initial_state(task),
                config={"recursion_limit": RECURSION_LIMIT},
                stream_mode="updates",
            ):
                for node, delta in update.items():
                    for event in _events_for(node, delta or {}):
                        yield event
    except GraphRecursionError:
        yield "error", {"message": "The agent hit its step limit before finishing. Please try again."}
        return

    yield "done", {}


# if __name__ == "__main__":
    # print(asyncio.run(run_agent("Check the orders table for anomalies.")))
