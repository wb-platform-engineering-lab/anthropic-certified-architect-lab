"""
exercise_5_builtin_tools.py — Claude Code's Five Built-In Tools

EXAM NOTE (Scenario 4 — Developer Productivity with Claude):
The exam names five built-in Claude Code tools. Know each tool's
exact capability, constraint, and when to use it vs a custom MCP tool.

The five tools:
  Read   — reads a file at a given path; respects .claudeignore
  Write  — writes or overwrites a file; creates parent dirs; no git commit
  Bash   — runs a shell command; returns stdout/stderr; prompts on destructive ops
  Grep   — searches file contents for a pattern
  Glob   — finds files matching a pattern; returns paths only (no contents)

IMPORTANT: These are Claude Code built-ins — available in a Claude Code
session. NOT automatically available in a direct API call unless registered.
"""

import json
import os
import glob as glob_module
import re
import subprocess
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Tool reference
# ---------------------------------------------------------------------------

print("""
Five built-in tools:
  Read   reads a file at a known path (not for discovery — use Glob)
  Write  writes/overwrites a file (does NOT commit to git)
  Bash   runs shell commands (prompts before destructive ops like rm -rf)
  Grep   searches file contents by regex pattern
  Glob   finds files by path pattern (**/*.py) — returns paths, not contents

Trust model:
  Built-ins operate within Anthropic-defined safety constraints.
  Custom MCP tools have NO inherited safety constraints — you own all validation.

Naming conflict:
  A custom tool named "Read" overrides the built-in Read silently.
  Never use built-in tool names for custom tools.
  Use: crm_read, s3_read, db_read — never "Read".
""")


# ---------------------------------------------------------------------------
# Simulated built-in tools
# ---------------------------------------------------------------------------

def builtin_read(file_path: str) -> dict:
    """Simulate Claude Code's Read tool."""
    try:
        path = Path(file_path)
        if not path.exists():
            return {"status": "access_failure", "code": "FILE_NOT_FOUND", "message": f"{file_path} does not exist."}
        content = path.read_text(encoding="utf-8")
        return {"status": "success", "content": content, "lines": len(content.splitlines()), "path": str(path)}
    except Exception as e:
        return {"status": "access_failure", "code": "READ_ERROR", "message": str(e)}


def builtin_write(file_path: str, content: str) -> dict:
    """Simulate Claude Code's Write tool. Creates parent dirs. Does NOT commit to git."""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"status": "success", "path": str(path), "bytes_written": len(content.encode())}
    except Exception as e:
        return {"status": "access_failure", "code": "WRITE_ERROR", "message": str(e)}


def builtin_bash(command: str, cwd: Optional[str] = None) -> dict:
    """
    Simulate Claude Code's Bash tool.
    In Claude Code, Bash prompts before destructive commands.
    This simulation blocks them instead.
    """
    dangerous = ["rm -rf", "git reset --hard", "DROP TABLE"]
    for pattern in dangerous:
        if pattern.lower() in command.lower():
            return {
                "status": "access_failure",
                "code": "DANGEROUS_COMMAND",
                "message": f"Command contains '{pattern}'. In Claude Code this would prompt for confirmation.",
            }
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd=cwd or os.getcwd(),
        )
        return {
            "status": "success" if result.returncode == 0 else "access_failure",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "access_failure", "code": "TIMEOUT", "message": "Command timed out after 30 seconds."}
    except Exception as e:
        return {"status": "access_failure", "code": "BASH_ERROR", "message": str(e)}


def builtin_grep(pattern: str, path: str = ".", recursive: bool = True) -> dict:
    """Simulate Claude Code's Grep tool."""
    try:
        matches = []
        search_path = Path(path)
        files = list(search_path.rglob("*")) if recursive else list(search_path.glob("*"))
        for f in files:
            if f.is_file():
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(text.splitlines(), 1):
                        if re.search(pattern, line):
                            matches.append({"file": str(f), "line": i, "content": line.strip()})
                except Exception:
                    pass
        if not matches:
            return {"status": "empty", "message": f"No files contain pattern: {pattern}"}
        return {"status": "success", "matches": matches[:50], "total_matches": len(matches)}
    except Exception as e:
        return {"status": "access_failure", "code": "GREP_ERROR", "message": str(e)}


def builtin_glob(pattern: str, base_path: str = ".") -> dict:
    """
    Simulate Claude Code's Glob tool.
    Returns sorted file paths — does NOT read contents.
    """
    try:
        base = Path(base_path)
        matches = sorted(str(p) for p in base.glob(pattern) if p.is_file())
        if not matches:
            return {"status": "empty", "message": f"No files match pattern: {pattern}"}
        return {"status": "success", "paths": matches, "count": len(matches)}
    except Exception as e:
        return {"status": "access_failure", "code": "GLOB_ERROR", "message": str(e)}


TOOL_REGISTRY = {
    "Read":  lambda args: builtin_read(**args),
    "Write": lambda args: builtin_write(**args),
    "Bash":  lambda args: builtin_bash(**args),
    "Grep":  lambda args: builtin_grep(**args),
    "Glob":  lambda args: builtin_glob(**args),
}

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

BUILTIN_TOOL_SCHEMAS = [
    {
        "name": "Read",
        "description": (
            "WHAT: Reads a file and returns its full text content.\n"
            "WHEN: Use when you know the exact path. Do NOT use to discover files — use Glob.\n"
            "SHAPES: status=success with content and line count. "
            "status=access_failure with FILE_NOT_FOUND if the path does not exist.\n"
            "ON FAILURE: Verify the path with Glob before retrying. Do not guess alternate paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": (
            "WHAT: Writes text to a file, creating missing parent directories. Overwrites existing content.\n"
            "WHEN: Use to create or overwrite files. Does NOT commit to git.\n"
            "SHAPES: status=success with path and bytes_written. "
            "status=access_failure with WRITE_ERROR on permission failure.\n"
            "ON FAILURE: Report to user — write failures usually indicate a permissions problem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Bash",
        "description": (
            "WHAT: Runs a shell command and returns stdout, stderr, and return code.\n"
            "WHEN: Use for tasks that Read/Write/Grep/Glob cannot do (run tests, git status, etc.). "
            "Destructive commands prompt for confirmation in Claude Code.\n"
            "SHAPES: status=success when returncode=0. "
            "status=access_failure with DANGEROUS_COMMAND if command was blocked. "
            "status=access_failure with TIMEOUT if command exceeded 30 seconds.\n"
            "ON FAILURE: For DANGEROUS_COMMAND, do not retry. Check stderr for non-zero returncode."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Grep",
        "description": (
            "WHAT: Searches file contents for a regex pattern, returning up to 50 matches.\n"
            "WHEN: Use to find where a symbol, import, or string appears across many files. "
            "Do NOT use to list files — use Glob for that.\n"
            "SHAPES: status=success with matches list and total_matches. "
            "status=empty means no files contain the pattern — not an error.\n"
            "ON FAILURE: If empty, widen the pattern or check the search path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Glob",
        "description": (
            "WHAT: Finds files matching a glob pattern (e.g. **/*.py) and returns sorted paths. "
            "Does NOT return file contents.\n"
            "WHEN: Use at the start of codebase exploration to discover files before reading them. "
            "Use Read or Grep afterward to inspect contents.\n"
            "SHAPES: status=success with paths list and count. "
            "status=empty means no files match — not an error.\n"
            "ON FAILURE: If empty, check the base_path and try a broader pattern."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "base_path": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
]


# ---------------------------------------------------------------------------
# Simple agentic demo
# ---------------------------------------------------------------------------

def run_explorer(task: str, target_dir: str) -> dict:
    """Run an agentic loop with all five tools. Max 6 iterations."""
    system = (
        "You are a codebase explorer. Use Glob to find files, Read to inspect them, "
        "Grep to find usages, Bash to run commands, Write to save reports. "
        "Use the right tool for each task."
    )
    messages = [{"role": "user", "content": f"Directory: {target_dir}\nTask: {task}"}]
    tool_calls = []

    for iteration in range(6):
        response = client.messages.create(
            model=MODEL, max_tokens=1024, system=system,
            tools=BUILTIN_TOOL_SCHEMAS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            final = " ".join(b.text for b in response.content if hasattr(b, "text"))
            return {"tool_calls": tool_calls, "response": final, "status": "completed"}

        if response.stop_reason != "tool_use":
            return {"tool_calls": tool_calls, "response": "", "status": response.stop_reason}

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_calls.append(block.name)
            print(f"  [iter {iteration+1}] {block.name}({list(block.input.keys())})")
            handler = TOOL_REGISTRY.get(block.name)
            if handler:
                try:
                    result = handler(block.input)
                except Exception as exc:
                    result = {"status": "access_failure", "code": "HANDLER_ERROR", "message": str(exc)}
            else:
                result = {"status": "access_failure", "code": "UNKNOWN_TOOL"}
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
        messages.append({"role": "user", "content": results})

    return {"tool_calls": tool_calls, "response": "Max iterations reached.", "status": "max_iterations"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    current_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("Exercise 5: Claude Code's Five Built-In Tools")
    print("=" * 60)
    print()

    # Demo: find Python files that import anthropic
    print("Task: Find Python files that import anthropic")
    print()
    result = run_explorer(
        task="Find all Python files in this directory and list which ones import the anthropic library.",
        target_dir=current_dir,
    )
    print()
    print(f"Tools used:  {' → '.join(result['tool_calls']) or '(none)'}")
    print(f"Status:      {result['status']}")
    print(f"Response:    {result['response'][:300]}")

    print()
    print("=" * 60)
    print("When to use each tool:")
    print("  Glob  — find files by path pattern (**/*.py)")
    print("  Read  — read a specific file whose path you know")
    print("  Grep  — find where a symbol appears across many files")
    print("  Bash  — run tests, git status, anything Read/Write/Grep/Glob can't do")
    print("  Write — save a report or generate a new file")
    print()
    print("When to use a custom MCP tool instead:")
    print("  Read a private S3 file     → Read only sees the local filesystem")
    print("  Query a PostgreSQL database → Bash requires hardcoded credentials")
    print("  Call an authenticated API   → Bash exposes tokens in command strings")
    print("=" * 60)


if __name__ == "__main__":
    main()
