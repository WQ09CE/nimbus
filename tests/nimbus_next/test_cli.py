"""Tests for nimbus_next.cli — the terminal interface."""

import pytest

from nimbus_next.cli import parse_args, Colors


class TestParseArgs:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli"])
        args = parse_args()
        assert args.goal is None
        assert args.model == "gpt-4o"
        assert args.provider == "openai"
        assert args.max_iterations == 50

    def test_one_shot(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli", "List files"])
        args = parse_args()
        assert args.goal == "List files"

    def test_anthropic_config(self, monkeypatch):
        monkeypatch.setattr("sys.argv", [
            "cli", "--provider", "anthropic",
            "--model", "claude-sonnet-4-20250514",
        ])
        args = parse_args()
        assert args.provider == "anthropic"
        assert args.model == "claude-sonnet-4-20250514"


class TestColors:
    def test_colors_are_strings(self):
        assert isinstance(Colors.RESET, str)
        assert isinstance(Colors.BOLD, str)
        assert Colors.RESET.startswith("\033[")
