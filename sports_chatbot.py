"""
Sports Expert Chatbot — core agent logic
-----------------------------------------
Connects to the office-weather MCP server (office_weather_servers.py, served
over Streamable HTTP) to pull two things at session start:

  - PROMPTS: the shared guardrails plus exactly one persona's instructions
    (Cricket Expert or Badminton Expert) — these become the system prompt.
  - TOOLS: get_weather, in case the locked persona needs to check whether
    weather affects play at one of the company's office locations.

The persona is chosen once, from the first user message, and locked for the
rest of the session — there is no mid-session switching between personas.
A standalone office-weather question (no sport mentioned) is answered
directly via get_weather instead, leaving the persona unlocked.

The LLM provider is chosen via the LLM_PROVIDER env var ("anthropic" or
"openai"); the matching *_API_KEY must also be set.

Requires: pip install "anthropic[mcp]" openai
"""

import json
import os
from typing import Literal

import anthropic
import openai
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
OFFICE_LOCATIONS_URI = AnyUrl("offices://locations")

MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o-mini",
}
PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
if PROVIDER not in MODELS:
    raise ValueError(
        f"Unsupported LLM_PROVIDER={PROVIDER!r}; expected one of {list(MODELS)}"
    )
MODEL = MODELS[PROVIDER]

AnyClient = anthropic.AsyncAnthropic | openai.AsyncOpenAI

Persona = Literal["cricket_expert", "badminton_expert"]

PERSONA_LABELS: dict[Persona, str] = {
    "cricket_expert": "Cricket Expert",
    "badminton_expert": "Badminton Expert",
}


def make_client() -> AnyClient:
    return anthropic.AsyncAnthropic() if PROVIDER == "anthropic" else openai.AsyncOpenAI()


async def classify_persona(
    client: AnyClient, message: str
) -> Persona | Literal["weather", "offices", "unclear"]:
    """
    Classify the first user message into a sports-expert persona, into
    "weather" if it's a standalone question about current weather/conditions
    at a company office, into "offices" if it's a standalone question about
    the office locations themselves (how many, where, which countries —
    not weather), or "unclear" if none of those apply (e.g. a bare
    greeting) — callers should ask the user to clarify in the unclear case
    rather than lock a persona.
    """
    prompt = (
        "Classify this user message into exactly one category:\n"
        "- cricket_expert: the message is about cricket.\n"
        "- badminton_expert: the message is about badminton.\n"
        "- weather: the message asks about current weather/conditions at a "
        "company office location, with no sport content.\n"
        "- offices: the message asks about the company's office locations "
        "themselves (e.g. how many there are, where they are, which "
        "countries) rather than the weather there, with no sport content.\n"
        "- unclear: none of the above applies (e.g. a bare greeting or "
        "small talk).\n"
        "Do not guess — pick unclear if genuinely ambiguous.\n\n"
        f"Message: {message!r}"
    )
    schema = {
        "type": "object",
        "properties": {
            "persona": {
                "type": "string",
                "enum": [*PERSONA_LABELS, "weather", "offices", "unclear"],
            }
        },
        "required": ["persona"],
        "additionalProperties": False,
    }

    if PROVIDER == "anthropic":
        response = await client.messages.create(
            model=MODEL,
            max_tokens=64,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = next(b.text for b in response.content if b.type == "text")
    else:
        response = await client.chat.completions.create(
            model=MODEL,
            max_tokens=64,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "persona_classification",
                    "strict": True,
                    "schema": schema,
                },
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content

    return json.loads(text)["persona"]


async def _fetch_system_prompt(session: ClientSession, persona: Persona) -> str:
    """Fetch the shared guardrails + persona-specific prompt from the MCP server."""
    persona_result = await session.get_prompt(persona)
    guardrails_result = await session.get_prompt("guardrails")
    persona_text = persona_result.messages[0].content.text
    guardrails_text = guardrails_result.messages[0].content.text
    return f"{persona_text}\n\n{guardrails_text}"


async def _fetch_offices(session: ClientSession) -> list[dict]:
    """Fetch the company's office directory from the offices://locations resource."""
    result = await session.read_resource(OFFICE_LOCATIONS_URI)
    return json.loads(result.contents[0].text)


async def _fetch_office_cities(session: ClientSession) -> str:
    """Fetch the company's office city names from the offices://locations resource."""
    offices = await _fetch_offices(session)
    return ", ".join(office["city"] for office in offices)


def _weather_only_prompt(office_cities: str) -> str:
    return (
        "You are the office weather assistant.\n"
        "Answer only questions about current weather conditions at the "
        f"company's office locations ({office_cities}), using "
        "the get_weather tool. Do not discuss cricket, badminton, or any other "
        "topic — if asked, say warmly and politely that this reply only covers "
        "office weather.\n"
        "Never fabricate weather data; only report what the tool returns.\n"
        "Tone: always encouraging, polite, and professional."
    )


def _offices_only_prompt(offices: list[dict]) -> str:
    return (
        "You are the office directory assistant.\n"
        "Answer only questions about the company's office locations, using "
        "ONLY this data — never fabricate an office that isn't listed here:\n"
        f"{json.dumps(offices)}\n"
        "Do not discuss cricket, badminton, current weather, or any other "
        "topic — if asked, say warmly and politely that this reply only "
        "covers office locations.\n"
        "Tone: always encouraging, polite, and professional."
    )


async def _run_turn_anthropic(
    api_client: anthropic.AsyncAnthropic,
    system_prompt: str,
    tools_result,
    mcp_session: ClientSession,
    history: list[dict],
    user_message: str,
):
    """Yields progress events, ending with {"type": "final", "text": ...}."""
    tools = [async_mcp_tool(t, mcp_session) for t in tools_result.tools]
    messages = history + [{"role": "user", "content": user_message}]

    runner = api_client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=tools,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=messages,
    )

    final = None
    seen_tool_calls = set()
    async for message in runner:
        final = message
        for block in message.content:
            if block.type == "tool_use" and block.id not in seen_tool_calls:
                seen_tool_calls.add(block.id)
                yield {"type": "tool_call", "name": block.name, "arguments": block.input}

    text = next((b.text for b in final.content if b.type == "text"), "(no response)")
    yield {"type": "final", "text": text}


async def _run_turn_openai(
    api_client: openai.AsyncOpenAI,
    system_prompt: str,
    tools_result,
    mcp_session: ClientSession,
    history: list[dict],
    user_message: str,
):
    """Yields progress events, ending with {"type": "final", "text": ...}."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools_result.tools
    ]
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_message}]
    )

    while True:
        response = await api_client.chat.completions.create(
            model=MODEL,
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )
        choice = response.choices[0].message

        if not choice.tool_calls:
            yield {"type": "final", "text": choice.content or "(no response)"}
            return

        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in choice.tool_calls
                ],
            }
        )
        for call in choice.tool_calls:
            arguments = json.loads(call.function.arguments)
            yield {"type": "tool_call", "name": call.function.name, "arguments": arguments}

            result = await mcp_session.call_tool(call.function.name, arguments)
            result_text = "\n".join(
                block.text for block in result.content if block.type == "text"
            )
            yield {"type": "tool_result", "name": call.function.name, "result": result_text}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                }
            )


async def _dispatch_turn(
    api_client: AnyClient,
    system_prompt: str,
    tools_result,
    mcp_session: ClientSession,
    history: list[dict],
    user_message: str,
):
    """Yields progress events from whichever provider is active."""
    gen = (
        _run_turn_anthropic(
            api_client, system_prompt, tools_result, mcp_session, history, user_message
        )
        if PROVIDER == "anthropic"
        else _run_turn_openai(
            api_client, system_prompt, tools_result, mcp_session, history, user_message
        )
    )
    async for event in gen:
        yield event


async def run_turn(
    api_client: AnyClient,
    persona: Persona,
    history: list[dict],
    user_message: str,
):
    """
    Run one chat turn against the locked persona, with the get_weather tool
    available. Yields progress events, ending with
    {"type": "final", "text": ...}.
    """
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()

            system_prompt = await _fetch_system_prompt(mcp_session, persona)
            tools_result = await mcp_session.list_tools()

            async for event in _dispatch_turn(
                api_client, system_prompt, tools_result, mcp_session, history, user_message
            ):
                yield event


async def answer_weather_query(
    api_client: AnyClient, history: list[dict], user_message: str
):
    """
    Answer a standalone office-weather question with the get_weather tool,
    without locking a sport persona. Used when the first message asks about
    office weather with no sport content — the session stays unlocked so a
    later message can still lock a persona normally. Yields progress
    events, ending with {"type": "final", "text": ...}.
    """
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()

            yield {"type": "resource_read", "uri": str(OFFICE_LOCATIONS_URI)}
            office_cities = await _fetch_office_cities(mcp_session)
            system_prompt = _weather_only_prompt(office_cities)
            tools_result = await mcp_session.list_tools()

            async for event in _dispatch_turn(
                api_client,
                system_prompt,
                tools_result,
                mcp_session,
                history,
                user_message,
            ):
                yield event


async def _simple_reply(
    api_client: AnyClient, system_prompt: str, history: list[dict], user_message: str
) -> str:
    """One untooled LLM turn — used when the answer is already fully grounded
    in data fetched from an MCP resource, so no tool-calling loop is needed."""
    messages = history + [{"role": "user", "content": user_message}]

    if PROVIDER == "anthropic":
        response = await api_client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        return next((b.text for b in response.content if b.type == "text"), "(no response)")

    response = await api_client.chat.completions.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "system", "content": system_prompt}] + messages,
    )
    return response.choices[0].message.content or "(no response)"


async def answer_offices_query(
    api_client: AnyClient, history: list[dict], user_message: str
):
    """
    Answer a standalone question about the company's office locations,
    grounded in the offices://locations MCP resource — no sport persona is
    locked and no tools are involved, since the resource data is already
    everything needed to answer. Yields {"type": "final", "text": ...}.
    """
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            yield {"type": "resource_read", "uri": str(OFFICE_LOCATIONS_URI)}
            offices = await _fetch_offices(mcp_session)

    system_prompt = _offices_only_prompt(offices)
    text = await _simple_reply(api_client, system_prompt, history, user_message)
    yield {"type": "final", "text": text}


async def handle_turn(
    persona: Persona | None,
    history: list[dict],
    user_message: str,
):
    """
    Single entry point for one chat turn: classifies the persona on the first
    message (if not already locked), then runs the turn. Uses one API client
    for both calls so everything runs on the same event loop.

    Yields progress events ({"type": "tool_call", ...} /
    {"type": "tool_result", ...}) as tools are called, ending with
    {"type": "done", "persona": ..., "text": ...}.

    If the message doesn't indicate either sport, no persona is locked and a
    clarifying question is returned instead — the next message tries again.
    A standalone office-weather question is answered directly and also
    leaves the persona unlocked.
    """
    client = make_client()
    if persona is None:
        classification = await classify_persona(client, user_message)
        if classification == "weather":
            async for event in answer_weather_query(client, history, user_message):
                if event["type"] == "final":
                    yield {"type": "done", "persona": None, "text": event["text"]}
                else:
                    yield event
            return
        if classification == "offices":
            async for event in answer_offices_query(client, history, user_message):
                if event["type"] == "final":
                    yield {"type": "done", "persona": None, "text": event["text"]}
                else:
                    yield event
            return
        if classification == "unclear":
            yield {
                "type": "done",
                "persona": None,
                "text": (
                    "Are you here to talk cricket or badminton? Let me know "
                    "and I'll bring in the right expert."
                ),
            }
            return
        persona = classification

    async for event in run_turn(client, persona, history, user_message):
        if event["type"] == "final":
            yield {"type": "done", "persona": persona, "text": event["text"]}
        else:
            yield event
