import asyncio
import os
from typing import Annotated, TypedDict
from dotenv import load_dotenv
from fastmcp import Client
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.graph import START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from mcp_tools import mcp

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_TURNS = 15

SYSTEM_INSTRUCTION = (
    "You are a data-quality agent for a Postgres database. You have tools to list "
    "tables, compute column statistics, and detect outliers or unusual row-count "
    "trends, and observe changes in schema. Investigate the schema for data anomalies, then summarize what you "
    "found in plain English. If nothing looks anomalous, say so."
    "For every tool you use, include a short summary of why you are using it and what you are looking for."
    "IF applicable, attempt to link anomalies to recent schema changes. If you find anomalies, suggest a course of action to fix them."
    "Finally, list all anomalous points from each table."
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

    async def agent_node(state: AgentState) -> dict:
        response = await acting_llm.ainvoke(state["messages"])
        for call in response.tool_calls:
            print(f"[tool call] {call['name']}({call['args']})")
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("planner", plan_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()


async def run_agent(task: str) -> str:
    async with Client(mcp) as mcp_client:
        tools = await load_mcp_tools(mcp_client.session)
        app = _build_graph(tools)

        initial_state: AgentState = {
            "messages": [SystemMessage(content=SYSTEM_INSTRUCTION), HumanMessage(content=task)],
            "plan": "",
        }
        # +2 for the planner step and its edge into the agent; the rest covers the
        # agent/tools ping-pong, mirroring the old MAX_TURNS-bounded loop.
        final_state = await app.ainvoke(
            initial_state,
            config={"recursion_limit": MAX_TURNS * 2 + 2},
        )

        final_message = final_state["messages"][-1]
        if isinstance(final_message, AIMessage) and not final_message.tool_calls:
            return final_message.text
        return "Stopped after max turns without a final answer."


if __name__ == "__main__":
    print(asyncio.run(run_agent("Check the orders table for anomalies.")))
