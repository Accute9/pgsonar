import asyncio
import os
from dotenv import load_dotenv
from fastmcp import Client
from google import genai
from google.genai import types

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

# async def test():
#     client = genai.Client(api_key=GEMINI_API_KEY)
#     async with Client(mcp) as mcp_client:
#         mcp_tools = await mcp_client.list_tools()
#         return mcp_tools



def _mcp_tool_to_declaration(tool) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description or "",
        parameters_json_schema=tool.inputSchema,
    )


def _tool_result_text(result) -> str:
    if getattr(result, "data", None) is not None:
        return str(result.data)
    return "\n".join(getattr(block, "text", str(block)) for block in result.content)


async def run_agent(task: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    async with Client(mcp) as mcp_client:
        mcp_tools = await mcp_client.list_tools()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[types.Tool(function_declarations=[_mcp_tool_to_declaration(t) for t in mcp_tools])],
        )

        messages = [types.Content(role="user", parts=[types.Part(text=task)])]

        for _ in range(MAX_TURNS):
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=messages,
                config=config,
            )
            candidate = response.candidates[0]
            messages.append(candidate.content)

            calls = [part.function_call for part in candidate.content.parts if part.function_call]
            if not calls:
                return response.text

            response_parts = []
            for call in calls:
                print(f"[tool call] {call.name}({dict(call.args)})")
                result = await mcp_client.call_tool(call.name, dict(call.args))
                response_parts.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": _tool_result_text(result)},
                    )
                )
            messages.append(types.Content(role="user", parts=response_parts))

        return "Stopped after max turns without a final answer."


if __name__ == "__main__":
    print(asyncio.run(run_agent("Check the orders table for anomalies.")))
    # print(asyncio.run(test()))