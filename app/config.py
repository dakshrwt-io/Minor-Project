"""Settings for the coding agent.

Values come from environment variables. A `.env` file in the project folder
is loaded first, so you can keep your API key there instead of typing it
into every terminal.
"""

import json
import os

from dotenv import load_dotenv

# Copy every KEY=VALUE line from .env into os.environ.
# Variables already set in the terminal are NOT overwritten.
load_dotenv()

# The model is reached through OpenRouter, which speaks the same API as OpenAI.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = os.getenv("AGENT_MODEL", "deepseek/deepseek-v4-flash")

# Hard limit on how many times the agent may ask the model per message.
# Stops the agent from looping forever.
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "10"))

# SQLite file where chat history is saved.
MEMORY_DB = os.getenv("AGENT_MEMORY_DB", "data/memory.sqlite3")

# Optional MCP servers, as a JSON list. Example:
# AGENT_MCP_SERVERS=[{"name": "demo", "command": "python", "args": ["server.py"]}]
MCP_SERVERS = json.loads(os.getenv("AGENT_MCP_SERVERS", "[]"))
