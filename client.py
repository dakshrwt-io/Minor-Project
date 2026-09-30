"""Terminal chat with the coding agent.

Start the server first (python -m uvicorn app.main:app), then in a second terminal:
    python client.py --repo "C:\\path\\to\\your\\project"
"""

import argparse
import json
import sys
import uuid

import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

console = Console()

CHANGE_TOOLS = {"create_file", "write_file", "edit_file"}  # their results contain a diff

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


def show_diff(text: str) -> None:
    """Print a change in colour: added lines green, removed lines red."""
    for line in text.splitlines():
        if line.startswith(("+++", "---")):
            style = "bold"  # the file names
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"  # "@@ -3,4 +3,5 @@" = where in the file the change is
        else:
            style = "dim"  # unchanged lines around the change
        # markup=False: print the text exactly, even if it contains [brackets]
        console.print("   " + line, style=style, markup=False)


def show_usage(usage: dict) -> None:
    """One grey line under the answer, e.g. "2 model calls | 3,120 tokens | $0.0004"."""
    tokens = usage["input_tokens"] + usage["output_tokens"]
    console.print(
        f"[dim]{usage['model_calls']} model calls | {tokens:,} tokens "
        f"({usage['input_tokens']:,} in, {usage['output_tokens']:,} out) | "
        f"${usage['cost']:.4f}[/dim]"
    )


def show(event: dict) -> None:
    """Print one event from the server."""
    kind = event["type"]
    if kind == "action":
        arguments = short(str(event["arguments"]), 200)  # file contents can be very long
        console.print(f"[cyan]-> {event['tool']}[/cyan] {escape(arguments)}")
    elif kind == "observation":
        changed_a_file = event["tool"] in CHANGE_TOOLS and not event["result"].startswith("Error")
        if changed_a_file:
            show_diff(event["result"])
        elif event["tool"] == "run_tests":
            colour = "green" if event["result"].startswith("Tests PASSED") else "red"
            first_line = event["result"].splitlines()[0]
            console.print(f"   {first_line}", style=f"bold {colour}", markup=False)
        else:
            console.print(f"[dim]   {escape(short(event['result']))}[/dim]")
    elif kind == "done":
        console.print(Panel(Markdown(event["summary"]), title="Agent", border_style="green"))
        show_usage(event["usage"])
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

    # Old Windows terminals cannot show some characters (like arrows or emoji) and
    # would crash. errors="replace" prints "?" for those characters instead.
    sys.stdout.reconfigure(errors="replace")

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
