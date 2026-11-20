"""Tests for scripts/changelog.py release notes and changelog generation."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "changelog.py"
_SPEC = importlib.util.spec_from_file_location("changelog", _SCRIPT_PATH)
changelog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(changelog)


class TestChangelogHelpers:
    def test_categorize_commits(self):
        entries = [
            ("Fix crash on empty config", "abc1234"),
            ("docs: Update README", "def5678"),
            ("Add unit tests for watcher", "1112223"),
            ("Implement WORK_DIR support", "4445556"),
            ("Bump version", "7778889"),
        ]
        result = changelog.categorize_commits(entries)
        assert result == {
            "Fixes": 1,
            "Features & Improvements": 1,
            "Documentation": 1,
            "Tests & CI": 1,
            "Other": 1,
        }

    def test_format_entries_empty(self):
        assert changelog.format_entries([]) == [
            "* Routine maintenance and internal improvements."
        ]

    def test_format_entries_non_empty(self):
        entries = [("Fix bug", "abc1234"), ("Add feature", "def5678")]
        assert changelog.format_entries(entries) == [
            "* Fix bug (abc1234)",
            "* Add feature (def5678)",
        ]


class TestGenerateReleaseNotes:
    def test_release_notes_with_previous_tag(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "gyengus/gitlab-watcher")

        def fake_run_git(args):
            if args[:2] == ["tag", "--sort=-creatordate"]:
                return "1.0.3\n1.0.2"
            if args[:3] == ["describe", "--tags", "--abbrev=0"]:
                return "1.0.2"
            if args[0] == "log" and "--no-merges" in args:
                return "Merge branch 'fixes' into 'master'|0000000\nFix timeout|b2e6711\nUpdate README|56b850b"
            if args[0] == "log" and "-1" in args:
                return "2026-04-10"
            if args[0] == "diff" and "--shortstat" in args:
                return "3 files changed, 42 insertions(+), 5 deletions(-)"
            return ""

        with patch.object(changelog, "run_git", side_effect=fake_run_git):
            notes = changelog.generate_release_notes("1.0.3")

        assert "## Release 1.0.3" in notes
        assert "### Summary" in notes
        assert "**Changes:** 2 commits since `1.0.2` (2026-04-10)" in notes
        assert "**Diff Stat:** 3 files changed, 42 insertions(+), 5 deletions(-)" in notes
        assert "**Breakdown:** 1 Fixes, 1 Documentation" in notes
        assert "### What's Changed" in notes
        assert "* Fix timeout (b2e6711)" in notes
        assert "* Update README (56b850b)" in notes
        assert "Merge branch" not in notes
        assert "https://github.com/gyengus/gitlab-watcher/compare/1.0.2...1.0.3" in notes
        assert "https://pypi.org/project/gitlab-watcher/" in notes

    def test_release_notes_without_previous_tag(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

        def fake_run_git(args):
            if args[:2] == ["tag", "--sort=-creatordate"]:
                return "1.0.0"
            if args[:3] == ["describe", "--tags", "--abbrev=0"]:
                return ""
            if args[0] == "log" and "--no-merges" in args:
                return "Initial commit|1234567"
            if args[0] == "log" and "-1" in args:
                return "2026-04-01"
            return ""

        with patch.object(changelog, "run_git", side_effect=fake_run_git):
            notes = changelog.generate_release_notes("v1.0.0")

        assert "## Release 1.0.0" in notes
        assert "**Changes:** 1 commit (2026-04-01)" in notes
        assert "* Initial commit (1234567)" in notes
        assert "Full Changelog" not in notes

