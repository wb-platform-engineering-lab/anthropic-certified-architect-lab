"""
exercise_5_builtin_tools.py — Claude Code's Five Built-In Tools

EXAM NOTE (Scenario 4 — Developer Productivity with Claude):
The exam explicitly names five built-in Claude Code tools. You must know
each tool's exact capability, constraint, and trust model.

The five tools:
  Read   — reads a file at a given path; respects .claudeignore
  Write  — writes or overwrites a file; creates parent dirs; no git commit
  Bash   — runs a shell command; returns stdout/stderr; prompts on destructive ops
  Grep   — searches file contents for a pattern; faster than Bash grep for large repos
  Glob   — finds files matching a pattern (**/*.ts); returns sorted paths; no file contents

IMPORTANT: These are Claude CODE built-ins — not general API tools.
They are available in a Claude Code session. They are NOT automatically available
in a direct API call or Agent SDK session unless explicitly registered.

This file simulates all five tools as Python functions, builds a codebase
exploration agent that uses all five in sequence, and shows which tasks
require a custom MCP tool instead of a built-in.
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
# Section 1: Built-in tool reference table
# ---------------------------------------------------------------------------

BUILTIN_TOOLS_REFERENCE = """
============================================================
CLAUDE CODE BUILT-IN TOOLS — Reference
============================================================

Tool   Reads?  Writes?  Executes?  Scope               Notes
------ ------- -------- --------- ------------------- ----------------------------
Read   YES     no       no        Project files        Respects .claudeignore
Write  no      YES      no        Project files        Creates dirs; no git commit
Bash   no      indirect YES       Shell + filesystem   Prompts on destructive cmds
Grep   YES     no       no        File contents        Pattern search; no regex limit
Glob   no      no       no        File paths only      Returns paths, not contents

Trust model:
  Built-in tools operate within Anthropic-defined constraints.
  Bash prompts for confirmation on destructive commands (rm, git reset, etc.).
  Custom MCP tools have NO inherited safety constraints — the developer owns all validation.

Naming conflict rule:
  If you define a custom tool named "Read", it overrides the built-in Read.
  This is a source of silent bugs. Never use built-in tool names for custom tools.

Availability:
  Built-ins are available in Claude Code sessions ONLY.
  A direct client.messages.create() call does NOT have these tools.
  An Agent SDK session does NOT have these tools unless explicitly registered.
============================================================
"""

# ---------------------------------------------------------------------------
# Section 2: Simulated built-in tools as Python functions
# ---------------------------------------------------------------------------

def builtin_read(file_path: str) -> dict:
    """Simulate Claude Code's Read tool."""
    try:
        path = Path(file_path)
        if not path.exists():
            return {
                "status": "access_failure",
                "code": "FILE_NOT_FOUND",
                "message": f"{file_path} does not exist.",
            }
        content = path.read_text(encoding="utf-8")
        return {
            "status": "success",
            "content": content,
            "lines": len(content.splitlines()),
            "path": str(path),
        }
    except Exception as e:
        return {"status": "access_failure", "code": "READ_ERROR", "message": str(e)}


def builtin_write(file_path: str, content: str) -> dict:
    """Simulate Claude Code's Write tool. Creates parent dirs. Does NOT commit to git."""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "status": "success",
            "path": str(path),
            "bytes_written": len(content.encode()),
        }
    except Exception as e:
        return {"status": "access_failure", "code": "WRITE_ERROR", "message": str(e)}


def builtin_bash(command: str, cwd: Optional[str] = None) -> dict:
    """
    Simulate Claude Code's Bash tool.
    NOTE: In Claude Code, Bash prompts for confirmation on destructive commands.
    This simulation does NOT prompt — it executes directly. Add your own guards.
    """
    # Safety check: refuse obviously destructive commands in this demo
    dangerous_patterns = ["rm -rf", "git reset --hard", "DROP TABLE", "format c:"]
    for pattern in dangerous_patterns:
        if pattern.lower() in command.lower():
            return {
                "status": "access_failure",
                "code": "DANGEROUS_COMMAND",
                "message": (
                    f"Command contains dangerous pattern: {pattern}. "
                    "In Claude Code, this would prompt for confirmation."
                ),
            }
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or os.getcwd(),
        )
        return {
            "status": "success" if result.returncode == 0 else "access_failure",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "access_failure",
            "code": "TIMEOUT",
            "message": "Command timed out after 30 seconds.",
        }
    except Exception as e:
        return {"status": "access_failure", "code": "BASH_ERROR", "message": str(e)}


def builtin_grep(pattern: str, path: str = ".", recursive: bool = True) -> dict:
    """Simulate Claude Code's Grep tool. Searches file contents for a pattern."""
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
                            matches.append({
                                "file": str(f),
                                "line": i,
                                "content": line.strip(),
                            })
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
    Returns sorted file paths matching the pattern. Does NOT read file contents.
    """
    try:
        base = Path(base_path)
        matches = sorted(str(p) for p in base.glob(pattern) if p.is_file())
        if not matches:
            return {"status": "empty", "message": f"No files match pattern: {pattern}"}
        return {"status": "success", "paths": matches, "count": len(matches)}
    except Exception as e:
        return {"status": "access_failure", "code": "GLOB_ERROR", "message": str(e)}


# ---------------------------------------------------------------------------
# Section 3: Tool schemas (for the Anthropic SDK agent)
# ---------------------------------------------------------------------------

BUILTIN_TOOL_SCHEMAS = [
    {
        "name": "Read",
        "description": (
            "WHAT: Reads a file at the given path and returns its full text content.\n"
            "WHEN: Use when you need the contents of a specific file whose path you already know. "
            "Do NOT use to search for files — use Glob for that. "
            "Do NOT use on files listed in .claudeignore.\n"
            "SHAPES: status=success includes content and line count. "
            "status=access_failure with code=FILE_NOT_FOUND means the path does not exist.\n"
            "ON FAILURE: If FILE_NOT_FOUND, verify the path with Glob before retrying. "
            "Do not guess alternate paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": (
            "WHAT: Writes text content to a file, creating the file and any missing parent "
            "directories. Overwrites existing content. Does NOT commit to git.\n"
            "WHEN: Use to save a report, generate a config file, or create a new source file. "
            "Do NOT use when the goal is to append — this tool overwrites.\n"
            "SHAPES: status=success includes the path written and bytes_written. "
            "status=access_failure with code=WRITE_ERROR means a permission or path error.\n"
            "ON FAILURE: Report the error to the user. Do not retry silently — "
            "write failures may indicate a permissions problem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path where the file will be written.",
                },
                "content": {
                    "type": "string",
                    "description": "Full text content to write to the file.",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Bash",
        "description": (
            "WHAT: Runs a shell command and returns stdout, stderr, and the return code.\n"
            "WHEN: Use for tasks that cannot be done with Read, Write, Grep, or Glob — "
            "for example, running tests, listing installed packages, or checking git status. "
            "Do NOT use for file discovery (use Glob) or content search (use Grep). "
            "Destructive commands (rm -rf, git reset --hard) will prompt for confirmation.\n"
            "SHAPES: status=success when returncode is 0 with stdout output. "
            "status=access_failure with code=DANGEROUS_COMMAND means the command was blocked. "
            "status=access_failure with code=TIMEOUT means the command exceeded 30 seconds.\n"
            "ON FAILURE: For DANGEROUS_COMMAND, do not retry. Report to the user. "
            "For non-zero returncode, inspect stderr before proceeding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for the command.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "Grep",
        "description": (
            "WHAT: Searches all files under a path for lines matching a regex pattern, "
            "returning up to 50 matches with file path, line number, and line content.\n"
            "WHEN: Use when you need to find where a symbol, import, or string appears "
            "across many files. Grep is faster than Bash grep for large repos because "
            "it is optimised for Claude Code's file index. "
            "Do NOT use when you need the full file — use Read for that.\n"
            "SHAPES: status=success includes matches list and total_matches count. "
            "status=empty means no files contain the pattern — not an error.\n"
            "ON FAILURE: If status=empty, widen the pattern or check the search path. "
            "Do not treat empty as an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for in file contents.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to current directory.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to search subdirectories. Defaults to true.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Glob",
        "description": (
            "WHAT: Finds files whose paths match a glob pattern (e.g. **/*.py) and returns "
            "a sorted list of matching paths. Does NOT return file contents.\n"
            "WHEN: Use at the start of any codebase exploration task to discover which files "
            "exist before reading them. Glob is faster and cheaper than Bash find for file "
            "discovery. Use Read or Grep afterward to inspect contents.\n"
            "SHAPES: status=success includes paths list and count. "
            "status=empty means no files match the pattern — not an error.\n"
            "ON FAILURE: If status=empty, check the base_path and pattern. "
            "Try a broader pattern such as **/* before concluding the directory is empty."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern such as **/*.py or src/**/*.ts.",
                },
                "base_path": {
                    "type": "string",
                    "description": "Base directory to search from. Defaults to current directory.",
                },
            },
            "required": ["pattern"],
        },
    },
]

# ---------------------------------------------------------------------------
# Section 4: Tool registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "Read": lambda args: builtin_read(**args),
    "Write": lambda args: builtin_write(**args),
    "Bash": lambda args: builtin_bash(**args),
    "Grep": lambda args: builtin_grep(**args),
    "Glob": lambda args: builtin_glob(**args),
}

# ---------------------------------------------------------------------------
# Section 5: The codebase exploration agent
# ---------------------------------------------------------------------------

def run_codebase_explorer(target_directory: str, task: str) -> dict:
    """
    Agentic loop (max 8 iterations) that explores a codebase and produces a report.
    Has access to all five simulated built-in tools.

    Returns a dict with:
      iterations     — number of agentic loop iterations used
      tool_calls     — list of tool names invoked (in order)
      final_response — Claude's final text answer
      status         — "completed" or "max_iterations_reached"
    """
    system_prompt = (
        "You are a codebase explorer for Resolve. "
        "Use the available tools to investigate the codebase and answer the task. "
        "Use Glob to find files, Read to inspect them, Grep to find usages, "
        "Bash to run tests, and Write to save your report."
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"Target directory: {target_directory}\n\n"
                f"Task: {task}"
            ),
        }
    ]

    tool_calls_log = []
    iterations = 0
    max_iterations = 8

    print(f"\n[Explorer] Starting codebase exploration in: {target_directory}")
    print(f"[Explorer] Task: {task}\n")

    while iterations < max_iterations:
        iterations += 1
        print(f"[Explorer] Iteration {iterations}/{max_iterations} ...")

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=BUILTIN_TOOL_SCHEMAS,
            messages=messages,
        )

        # Append the assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Check stop reason
        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            print(f"[Explorer] Agent finished after {iterations} iteration(s).")
            return {
                "iterations": iterations,
                "tool_calls": tool_calls_log,
                "final_response": final_text,
                "status": "completed",
            }

        if response.stop_reason != "tool_use":
            # Unexpected stop — surface it gracefully
            return {
                "iterations": iterations,
                "tool_calls": tool_calls_log,
                "final_response": f"Unexpected stop_reason: {response.stop_reason}",
                "status": "completed",
            }

        # Execute each tool the model requested
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            tool_calls_log.append(tool_name)

            print(f"  -> Tool call: {tool_name}({json.dumps(tool_input)})")

            handler = TOOL_REGISTRY.get(tool_name)
            if handler is None:
                result_content = json.dumps({
                    "status": "access_failure",
                    "code": "UNKNOWN_TOOL",
                    "message": f"No handler registered for tool: {tool_name}",
                })
            else:
                try:
                    result = handler(tool_input)
                    result_content = json.dumps(result)
                except Exception as exc:
                    # Never raise uncaught exceptions from tool handlers
                    result_content = json.dumps({
                        "status": "access_failure",
                        "code": "HANDLER_EXCEPTION",
                        "message": str(exc),
                    })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_content,
            })

        messages.append({"role": "user", "content": tool_results})

    # Reached max iterations without end_turn
    return {
        "iterations": iterations,
        "tool_calls": tool_calls_log,
        "final_response": "Max iterations reached without a final answer.",
        "status": "max_iterations_reached",
    }


# ---------------------------------------------------------------------------
# Section 6: Tasks that REQUIRE a custom MCP tool
# ---------------------------------------------------------------------------

TASKS_REQUIRING_CUSTOM_MCP = {
    "Read a file from private S3": {
        "why_builtin_fails": (
            "Read only accesses local filesystem paths. "
            "It cannot authenticate to S3 or any external storage."
        ),
        "solution": "Custom MCP tool: s3_read(bucket, key) that uses boto3 with IAM credentials.",
    },
    "Query an internal PostgreSQL database": {
        "why_builtin_fails": (
            "Bash can run psql if installed locally, but cannot connect to an authenticated "
            "internal DB without credentials. Bash also has no type-safe result parsing."
        ),
        "solution": "Custom MCP tool: db_query(sql) with connection pooling and typed result shapes.",
    },
    "Call an authenticated internal API": {
        "why_builtin_fails": (
            "Bash can run curl, but credentials must be hardcoded in the command string "
            "(visible in logs). No retry logic, no typed error handling."
        ),
        "solution": (
            "Custom MCP tool: internal_api_call(endpoint, method, body) "
            "with OAuth token management."
        ),
    },
    "Read from a .claudeignore'd file": {
        "why_builtin_fails": (
            "Read respects .claudeignore — files listed there cannot be read by the built-in tool."
        ),
        "solution": (
            "Either remove from .claudeignore, or use a custom MCP tool with explicit file access "
            "(bypasses .claudeignore, so use carefully)."
        ),
    },
}

# ---------------------------------------------------------------------------
# Section 7: Naming conflict demonstration
# ---------------------------------------------------------------------------

NAMING_CONFLICT_EXAMPLE = """
NAMING CONFLICT RULE:
If you define a custom tool named "Read", it overrides Claude Code's built-in Read.

Example of the bug this causes:
  # Custom tool registered with the same name
  custom_tools = [{"name": "Read", "description": "Reads from our internal API..."}]

  # Claude Code now uses your custom "Read" when it needs to read a file.
  # It calls: Read(file_path="/etc/passwd")
  # Your API handler receives file_path="/etc/passwd" as an API endpoint.
  # It fails silently. Claude Code thinks file reading is broken.

  # Fix: name your custom tools distinctly.
  # Use: "crm_read", "s3_read", "db_read" — never "Read".
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Print the built-in tools reference table
    print(BUILTIN_TOOLS_REFERENCE)

    # 2. Run the codebase explorer on the current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    exploration_task = (
        "Find all Python files, count how many use the anthropic library, "
        "and list them in a report file called exploration_report.txt"
    )
    result = run_codebase_explorer(current_dir, exploration_task)

    print("\n" + "=" * 60)
    print("CODEBASE EXPLORATION RESULT")
    print("=" * 60)
    print(f"Status     : {result['status']}")
    print(f"Iterations : {result['iterations']}")
    print(f"Tools used : {' -> '.join(result['tool_calls']) or '(none)'}")
    print(f"\nFinal response:\n{result['final_response']}")

    # 3. Print tasks that require custom MCP tools
    print("\n" + "=" * 60)
    print("TASKS THAT REQUIRE A CUSTOM MCP TOOL")
    print("=" * 60)
    for task_name, info in TASKS_REQUIRING_CUSTOM_MCP.items():
        print(f"\nTask   : {task_name}")
        print(f"  Why built-in fails : {info['why_builtin_fails']}")
        print(f"  Solution           : {info['solution']}")

    # 4. Print the naming conflict example
    print("\n" + "=" * 60)
    print("NAMING CONFLICT EXAMPLE")
    print("=" * 60)
    print(NAMING_CONFLICT_EXAMPLE)

    # 5. Print exam summary
    print("=" * 60)
    print("EXAM NOTE (Scenario 4):")
    print("=" * 60)
    print("""
  Q: An agent needs to read from a private S3 bucket. Which tool?
  A: Custom MCP tool — built-in Read only accesses the local filesystem.

  Q: An agent needs to find all TypeScript files in a repo. Which tool?
  A: Glob with pattern **/*.ts — faster than Bash for file discovery.

  Q: An agent runs Bash rm -rf ./temp. What does Claude Code do?
  A: Prompts for confirmation before executing. Custom MCP tools do NOT do this.
""")


if __name__ == "__main__":
    main()
