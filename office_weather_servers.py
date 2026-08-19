"""
MCP Demo Server
----------------
Demonstrates the two core primitives together:

  - a RESOURCE: static/internal data the host can read (our office locations)
  - a TOOL: an action the model can call (fetch live weather for a location)

Run with:  python office_weather_servers.py
Serves over Streamable HTTP at http://127.0.0.1:8000/mcp — point your
MCP host (or the Inspector, in "Streamable HTTP" mode) at that URL.

Requires:  pip install "mcp[cli]" requests
Uses the free Open-Meteo API — no API key needed.
"""

import json
import os
import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Update this if ngrok assigns a new domain (free-tier domains change on restart).
NGROK_HOST = "a7a6-2401-4900-1c85-8a81-7737-ae98-9d5c-3f83.ngrok-free.app"

mcp = FastMCP(
    "office-weather-demo",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8000")),
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", NGROK_HOST],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            f"https://{NGROK_HOST}",
        ],
    ),
)

# ---------------------------------------------------------------------------
# RESOURCE: our office locations (this is "data the model can read")
# Just city names — no coordinates needed here.
# ---------------------------------------------------------------------------

OFFICES = [
    {"name": "Office 1", "city": "Delhi", "country": "India"},
    {"name": "Office 2", "city": "Charleston", "country": "USA"},
    {"name": "Office 3", "city": "Mohali", "country": "India"},
    {"name": "Office 4", "city": "Kolkata", "country": "India"},
]
OFFICE_CITIES = ", ".join(o["city"] for o in OFFICES)


@mcp.resource("offices://locations")
def get_office_locations() -> str:
    """Returns the list of company office locations (by city name)."""
    return json.dumps(OFFICES, indent=2)


# ---------------------------------------------------------------------------
# TOOL: get_weather (this is "an action the model can invoke")
# Takes a plain city name, geocodes it, then fetches current weather.
# ---------------------------------------------------------------------------

def _geocode(city: str) -> dict:
    """Look up latitude/longitude for a city name using Open-Meteo's free geocoder."""
    resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        raise ValueError(f"Could not find a location matching '{city}'")
    return results[0]  # contains latitude, longitude, name, country


def _resolve_locations(query: str) -> list[str]:
    """
    Resolve a query to all matching known office cities.

    Matches (case-insensitively) against office name, city, or country, so a
    country query (e.g. "India") returns every office in that country.
    Raises ValueError if the query doesn't match any valid office.
    """
    q = query.strip().lower()
    matches = []
    for office in OFFICES:
        candidates = {
            office["name"].lower(),
            office["city"].lower(),
            office["country"].lower(),
        }
        if q in candidates:
            matches.append(office["city"])
    if not matches:
        valid = ", ".join(
            f'"{o["name"]}" ({o["city"]}, {o["country"]})' for o in OFFICES
        )
        raise ValueError(
            f"'{query}' is not a valid office location. Valid offices: {valid}"
        )
    return matches


def _weather_report(city: str) -> str:
    """Fetch and format the current weather for a single geocodable city."""
    place = _geocode(city)
    weather_resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
        },
        timeout=10,
    )
    weather_resp.raise_for_status()
    data = weather_resp.json()["current"]

    label = f"{place['name']}, {place.get('country', '')}".strip(", ")
    return (
        f"Weather at {label}: "
        f"{data['temperature_2m']}°C, "
        f"wind {data['wind_speed_10m']} km/h "
        f"(weather code {data['weather_code']})"
    )


@mcp.tool()
def get_weather(location: str) -> str:
    """
    Fetch current weather for one of our office locations only. Accepts,
    case-insensitively:
      - an office name (e.g. "Office 1")
      - a city (e.g. "Delhi")
      - a country (e.g. "India" — returns weather for every office there)

    Args:
        location: Office name, city, or country to get weather for.
    """
    cities = _resolve_locations(location)
    return "\n".join(_weather_report(city) for city in cities)


# ---------------------------------------------------------------------------
# PROMPTS: persona + guardrail text for the sports-expert chatbot.
# The chatbot client fetches these at session start and uses them as its
# system prompt — guardrails are common to every persona; each persona adds
# its own scope on top, with no topical overlap between personas.
# ---------------------------------------------------------------------------


@mcp.prompt()
def guardrails() -> str:
    """Guardrails shared by every sports-expert persona."""
    return (
        "GUARDRAILS (apply regardless of persona):\n"
        "- Stay strictly within your assigned sport. If the user asks about "
        "any other sport (including the other persona's sport) or an "
        "unrelated topic, warmly and politely decline, briefly explain you "
        "can only discuss your assigned sport in this session, and invite "
        "them to ask a question in that domain instead — do not answer the "
        "off-topic question.\n"
        "- Never fabricate statistics, records, scores, or player facts. If "
        "you are not certain, say so explicitly instead of guessing.\n"
        "- Do not give betting/gambling advice or odds, medical or injury "
        "diagnoses, or collect personal data from the user.\n"
        "- You may call the get_weather tool only if the user asks whether "
        "weather could affect play at one of the company's office locations "
        f"({OFFICE_CITIES}). Do not call tools otherwise.\n"
        "- Tone: always encouraging, polite, and professional. Use "
        "respectful, professional language at all times. Never be curt, "
        "dismissive, or abusive, even when declining an off-topic or "
        "inappropriate request."
    )


@mcp.prompt()
def cricket_expert() -> str:
    """Persona instructions for the Cricket Expert."""
    return (
        "You are the Cricket Expert.\n"
        "Your sole domain is cricket: formats (Test, ODI, T20), laws and "
        "rules (LBW, DRS, powerplays, follow-on, etc.), history, teams, "
        "players, tournaments (World Cup, IPL, Ashes, and others), strategy, "
        "and statistics.\n"
        "You do not discuss badminton or any other sport — that is the "
        "Badminton Expert's domain, a separate persona in a separate "
        "session. If asked, say you're the Cricket Expert and can't cover "
        "that here."
    )


@mcp.prompt()
def badminton_expert() -> str:
    """Persona instructions for the Badminton Expert."""
    return (
        "You are the Badminton Expert.\n"
        "Your sole domain is badminton: rules and scoring, techniques "
        "(smash, drop shot, clear, net play), equipment (rackets, "
        "shuttlecocks, stringing), tournaments (BWF World Tour, Olympics, "
        "Thomas Cup, Uber Cup), players, and training/fitness advice.\n"
        "You do not discuss cricket or any other sport — that is the "
        "Cricket Expert's domain, a separate persona in a separate "
        "session. If asked, say you're the Badminton Expert and can't cover "
        "that here."
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")