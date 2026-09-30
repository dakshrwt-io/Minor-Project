"""Terminal chat with the coding agent.

Start the server first (python -m uvicorn app.main:app), then in a second terminal:
    python client.py --repo "C:\\path\\to\\your\\project"
"""

import argparse
import json
import uuid

import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

console = Console()

HELP = """Commands:
  /apply         turn file changes on or off
  /repo <path>   switch to another repository (starts a new chat)
  /new           start a new chat (forget earlier messages)
  /help          show this help
  /quit          exit"""


def short(text: str, limit: int = 300) -> str:
    """Shorten long tool results so the screen stays readable."""
    if len(text) <= limit:
        return text
    return text[:limit] + " ..."


def show(event: dict) -> None:
    """Print one event from the server."""
    kind = event["type"]
    if kind == "action":
        console.print(f"[cyan]-> {event['tool']}[/cyan] {escape(str(event['arguments']))}")
    elif kind == "observation":
        console.print(f"[dim]   {escape(short(event['result']))}[/dim]")
    elif kind == "done":
        console.print(Panel(Markdown(event["summary"]), title="Agent", border_style="green"))
    elif kind == "warning":
        console.print(f"[yellow]Warning: {escape(event['message'])}[/yellow]")
    elif kind == "error":
        console.print(f"[red]Error: {escape(event['message'])}[/red]")


def send(server: str, task: str, repo: str, apply_changes: bool, session_id: str) -> None:
    """Send one message to the server and print the events as they arrive."""
    payload = {
        "task": task,
        "target_repo": repo,
        "apply_changes": apply_changes,
        "session_id": session_id,
    }
    try:
        with httpx.stream("POST", f"{server}/run", json=payload, timeout=None) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    show(json.loads(line[len("data: ") :]))
    except httpx.ConnectError:
        console.print(f"[red]Cannot reach the server at {server}. Is uvicorn running?[/red]")


def print_header(repo: str, apply_changes: bool) -> None:
    changes = "[green]ON[/green]" if apply_changes else "[yellow]OFF[/yellow]"
    console.print(f"Repository: [bold]{escape(repo)}[/bold]   File changes: {changes}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the coding agent.")
    parser.add_argument("--repo", required=True, help="folder of the project to work on")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="agent server address")
    args = parser.parse_args()

    repo = args.repo
    apply_changes = False
    session_id = str(uuid.uuid4())  # a random id; same id = same conversation

    console.print("[bold]Coding Agent[/bold]  (type /help for commands)")
    print_header(repo, apply_changes)

    while True:
        try:
            line = console.input("[bold blue]you> [/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue
        elif line in ("/quit", "/exit"):
            break
        elif line == "/help":
            console.print(HELP)
        elif line == "/apply":
            apply_changes = not apply_changes
            print_header(repo, apply_changes)
        elif line == "/new":
            session_id = str(uuid.uuid4())
            console.print("Started a new chat.")
        elif line.startswith("/repo "):
            repo = line[len("/repo ") :].strip()
            session_id = str(uuid.uuid4())  # new repo = new chat, so the map is rebuilt
            print_header(repo, apply_changes)
        elif line.startswith("/"):
            console.print("Unknown command. Type /help.")
        else:
            send(args.server, line, repo, apply_changes, session_id)

    console.print("Bye!")


if __name__ == "__main__":
    main()
