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

# Documentation search (RAG): which folder of the target repository is indexed,
# which model turns text into numbers ("embeddings"), and where ChromaDB saves them.
DOCS_FOLDER = os.getenv("AGENT_DOCS_FOLDER", "docs")
EMBEDDING_MODEL = os.getenv("AGENT_EMBEDDING_MODEL", "openai/text-embedding-3-small")
VECTOR_DB = os.getenv("AGENT_VECTOR_DB", "data/vector_db")

# Command the run_tests tool runs inside the target repository.
# The model can only say "run the tests", it can never choose this command.
# .split() turns "python -m pytest -q" into ["python", "-m", "pytest", "-q"].
TEST_COMMAND = os.getenv("AGENT_TEST_COMMAND", "python -m pytest -q").split()

# Optional MCP servers, as a JSON list. Example:
# AGENT_MCP_SERVERS=[{"name": "demo", "command": "python", "args": ["server.py"]}]
MCP_SERVERS = json.loads(os.getenv("AGENT_MCP_SERVERS", "[]"))
