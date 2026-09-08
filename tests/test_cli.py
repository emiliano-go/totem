"""CLI smoke tests via click's CliRunner. Guards group-option ordering
(--project is a group option and must precede the subcommand)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from totem_mcp.cli import cli


def test_search_on_empty_db(project_dir):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--project", str(project_dir), "search", "--query", "x"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_create_and_list_round_trip(project_dir):
    runner = CliRunner()
    create = runner.invoke(
        cli,
        [
            "--project",
            str(project_dir),
            "create",
            "--type",
            "gotcha",
            "--title",
            "CLI smoke gotcha",
            "--statement",
            "created from the CLI",
            "--tags",
            "cli,smoke",
        ],
    )
    assert create.exit_code == 0, create.output
    item_id = json.loads(create.output)["id"]

    listing = runner.invoke(
        cli, ["--project", str(project_dir), "list", "--tags", "cli"]
    )
    assert listing.exit_code == 0, listing.output
    items = json.loads(listing.output)
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["tags"] == ["cli", "smoke"]

    got = runner.invoke(cli, ["--project", str(project_dir), "get", item_id])
    assert got.exit_code == 0, got.output
    assert json.loads(got.output)["title"] == "CLI smoke gotcha"
