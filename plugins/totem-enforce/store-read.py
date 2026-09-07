#!/usr/bin/env python3
"""Store implementation info after file read (PostToolUse hook).

Automatically creates an implementation memory for files the agent reads,
preventing re-reading the same code in future sessions.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Main hook entry point."""
    input_data = sys.stdin.read()
    try:
        payload = json.loads(input_data)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("filePath", "")
    project_dir = payload.get("cwd", os.getcwd())

    if not file_path:
        sys.exit(0)

    # Check if we already have implementation info for this file
    try:
        result = subprocess.run(
            [
                "totem", "search",
                "--query", file_path,
                "--types", "implementation",
                "--limit", "1",
                "--project", project_dir,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            existing = json.loads(result.stdout) if result.stdout.strip() else []
            if existing:
                sys.exit(0)  # Already have info for this file
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    # Store implementation memory
    file_name = Path(file_path).name
    try:
        subprocess.run(
            [
                "totem", "create",
                "--type", "implementation",
                "--title", f"File: {file_name}",
                "--statement", f"Agent read {file_path}",
                "--tags", f"implementation,read,{file_name},{file_path}",
                "--project", project_dir,
                "--metadata", json.dumps({
                    "subject": file_path,
                    "kind": "module",
                    "path": file_path,
                }),
            ],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
