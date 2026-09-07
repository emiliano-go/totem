#!/usr/bin/env python3
"""Clear hook state at turn boundary (UserPromptSubmit hook).

Resets the searched_this_turn state so the agent gets fresh blocking decisions
on each new user message.
"""

import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    session_id = os.environ.get("SESSION_ID", os.getppid())
    state_path = Path(tempfile.gettempdir()) / f"totem-hook-state-{session_id}.json"
    if state_path.exists():
        state_path.write_text(json.dumps({"searched_this_turn": {}}))


if __name__ == "__main__":
    main()
