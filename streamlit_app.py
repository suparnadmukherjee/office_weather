"""
Sports Expert Chatbot — Streamlit UI
-------------------------------------
Run with:       streamlit run streamlit_app.py
Requires the MCP server running first (separate terminal):
                python office_weather_servers.py

Ask a cricket or badminton question to start. The persona (Cricket Expert
or Badminton Expert) is chosen automatically from your first message and
locked for the rest of this browser session — click "Start new session" to
pick a different persona.
"""

import asyncio
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from sports_chatbot import PERSONA_LABELS, handle_turn

st.set_page_config(page_title="Sports Expert Chatbot", page_icon="🏆")

if "persona" not in st.session_state:
    st.session_state.persona = None
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("🏆 Sports Expert Chatbot")

if st.session_state.persona:
    st.caption(
        f"Talking to: **{PERSONA_LABELS[st.session_state.persona]}** "
        "(locked for this session)"
    )
else:
    st.caption(
        "Ask a cricket or badminton question to begin — the expert persona "
        "is chosen automatically from your first message and locked for "
        "this session."
    )

if st.button("Start new session"):
    st.session_state.persona = None
    st.session_state.messages = []
    st.rerun()

provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
required_key = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
if not os.environ.get(required_key):
    st.warning(f"Set the {required_key} environment variable before chatting.")

def _format_event(event: dict) -> str:
    if event["type"] == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in event["arguments"].items())
        return f"🔧 called tool `{event['name']}`({args})"
    if event["type"] == "tool_result":
        return f"✅ tool `{event['name']}` returned a result"
    if event["type"] == "resource_read":
        return f"📄 read resource `{event['uri']}`"
    return str(event)


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("trace"):
            st.caption("  \n".join(msg["trace"]))
        st.markdown(msg["content"])

user_input = st.chat_input("Ask your cricket or badminton question...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]

    with st.chat_message("assistant"):
        trace_placeholder = st.empty()
        trace: list[str] = []

        async def _drive():
            result = None
            async for event in handle_turn(st.session_state.persona, history, user_input):
                if event["type"] == "done":
                    result = (event["persona"], event["text"])
                else:
                    trace.append(_format_event(event))
                    trace_placeholder.caption("  \n".join(trace))
            return result

        persona, reply = asyncio.run(_drive())
        st.session_state.persona = persona
        st.markdown(reply)

    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "trace": trace}
    )
