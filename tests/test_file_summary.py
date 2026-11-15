"""Deterministic file-state summaries."""

from totem_mcp.file_summary import summarize_file


def test_python_summary_uses_docstring_and_symbols():
    text = '''"""Auth helpers."""

MAX_RETRIES = 3

class Token:
    """A refresh token."""

def rotate(token: str, ttl: int = 24) -> str:
    """Rotate a token."""
    return token
'''
    summary = summarize_file("src/auth.py", text)
    assert summary.language == "python"
    assert "Auth helpers" in summary.tldr
    assert "rotate" in summary.tldr
    assert {symbol.name for symbol in summary.symbols} >= {"Token", "rotate", "MAX_RETRIES"}
    rotate = next(symbol for symbol in summary.symbols if symbol.name == "rotate")
    assert "ttl" in rotate.signature
    assert rotate.detail == "Rotate a token."


def test_markdown_summary_uses_title_and_sections():
    text = "# Watcher\n\nHow the watcher works.\n\n## Install\n\nRun it.\n"
    summary = summarize_file("README.md", text)
    assert summary.language == "markdown"
    assert "Watcher" in summary.tldr
    assert "Install" in summary.tldr
    assert {symbol.name for symbol in summary.symbols} == {"Watcher", "Install"}


def test_json_and_yaml_summaries():
    json_summary = summarize_file("config.json", '{"a": 1, "b": 2}')
    assert "2 keys" in json_summary.tldr and "a, b" in json_summary.tldr
    yaml_summary = summarize_file("config.yml", "service: watcher\nimage: pinned\n")
    assert "YAML configuration" in yaml_summary.tldr
    assert {"service", "image"} <= {symbol.name for symbol in yaml_summary.symbols}


def test_javascript_summary():
    text = "import x from 'x'\nexport function run() {}\nexport class Worker {}\n"
    summary = summarize_file("src/worker.js", text)
    assert "run" in summary.tldr and "Worker" in summary.tldr
    assert {"run", "Worker"} <= {symbol.name for symbol in summary.symbols}


def test_invalid_python_falls_back():
    summary = summarize_file("broken.py", "def broken(:\n")
    assert summary.tldr
    assert summary.symbols == []
