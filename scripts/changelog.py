#!/usr/bin/env python3
"""
Generate release notes or full CHANGELOG.md from git history and tags.

Usage:
    python3 scripts/changelog.py --release-notes [TAG]
    python3 scripts/changelog.py --full
"""

import argparse
import os
import re
import subprocess
from datetime import datetime


def run_git(args):
    """Run a git command and return stripped stdout, or empty string on error."""
    try:
        res = subprocess.check_output(["git"] + args, stderr=subprocess.DEVNULL)
        return res.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def get_tags():
    """Return list of tags sorted by tag creator date (newest first)."""
    raw = run_git(["tag", "--sort=-creatordate"])
    if not raw:
        return []
    return [t.strip() for t in raw.splitlines() if t.strip()]


def get_commit_log(rev_range=None):
    """Return list of (subject, short_hash) tuples, omitting merge commits."""
    cmd = ["log", "--no-merges", "--format=%s|%h"]
    if rev_range:
        cmd.append(rev_range)
    raw = run_git(cmd)
    if not raw:
        return []

    entries = []
    for line in raw.splitlines():
        if not line.strip() or "|" not in line:
            continue
        subject, short_hash = line.strip().split("|", 1)
        if subject.startswith("Merge branch") or subject.startswith("Merge remote"):
            continue
        entries.append((subject, short_hash))
    return entries


def get_tag_date(tag):
    """Return YYYY-MM-DD date for the given tag."""
    raw = run_git(["log", "-1", "--format=%ad", "--date=short", tag])
    return raw or datetime.now().strftime("%Y-%m-%d")


def get_diff_shortstat(rev_range):
    """Return git diff --shortstat summary for a revision range."""
    if not rev_range or ".." not in rev_range:
        return ""
    return run_git(["diff", "--shortstat", rev_range])


def get_github_repo():
    """Return 'owner/repo' from GITHUB_REPOSITORY or git remote origin."""
    env_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if env_repo:
        return env_repo
    remote_url = run_git(["remote", "get-url", "origin"])
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", remote_url)
    return match.group(1) if match else ""


def categorize_commits(entries):
    """Count commits by high-level category based on commit subject."""
    categories = {
        "Fixes": 0,
        "Features & Improvements": 0,
        "Documentation": 0,
        "Tests & CI": 0,
        "Other": 0,
    }
    for subject, _ in entries:
        lower = subject.lower()
        if re.match(r"^(fix|bugfix|hotfix)\b", lower) or " fix " in lower:
            categories["Fixes"] += 1
        elif re.match(r"^(docs?|readme)\b", lower) or "documentation" in lower or "readme" in lower:
            categories["Documentation"] += 1
        elif re.match(r"^(test|ci|build)\b", lower) or "test" in lower or "pipeline" in lower or "workflow" in lower:
            categories["Tests & CI"] += 1
        elif re.match(r"^(feat|add|implement|make|update|support|improve|refactor|enable|clean|remove)\b", lower):
            categories["Features & Improvements"] += 1
        else:
            categories["Other"] += 1
    return {k: v for k, v in categories.items() if v > 0}


def format_entries(entries):
    """Format commit entries as markdown bullet points."""
    if not entries:
        return ["* Routine maintenance and internal improvements."]
    return [f"* {subject} ({short_hash})" for subject, short_hash in entries]


def build_summary_lines(entries, prev_tag=None, target_tag=None, rev_range=None):
    """Build markdown summary lines for a release."""
    commit_count = len(entries)
    commit_word = "commit" if commit_count == 1 else "commits"

    lines = ["### Summary", ""]
    if prev_tag and target_tag:
        tag_date = get_tag_date(target_tag)
        lines.append(f"- **Changes:** {commit_count} {commit_word} since `{prev_tag}` ({tag_date})")
    elif target_tag:
        tag_date = get_tag_date(target_tag)
        lines.append(f"- **Changes:** {commit_count} {commit_word} ({tag_date})")
    else:
        lines.append(f"- **Changes:** {commit_count} {commit_word}")

    shortstat = get_diff_shortstat(rev_range)
    if shortstat:
        lines.append(f"- **Diff Stat:** {shortstat}")

    categories = categorize_commits(entries)
    if categories:
        breakdown = ", ".join(f"{count} {name}" for name, count in categories.items())
        lines.append(f"- **Breakdown:** {breakdown}")

    lines.append("")
    return lines


def generate_release_notes(target_tag=None):
    """Generate markdown release notes for target_tag (or current CI/latest tag)."""
    tags = get_tags()
    if not target_tag:
        ci_tag = os.environ.get("CI_COMMIT_TAG") or (
            os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
        )
        target_tag = ci_tag or (tags[0] if tags else None)

    if not target_tag:
        entries = get_commit_log()[:20]
        lines = ["## Release Notes", ""]
        lines.extend(build_summary_lines(entries))
        lines.extend(["### What's Changed", ""])
        lines.extend(format_entries(entries))
        return "\n".join(lines)

    prev_tag = run_git(["describe", "--tags", "--abbrev=0", f"{target_tag}^"])
    rev_range = f"{prev_tag}..{target_tag}" if prev_tag else target_tag

    entries = get_commit_log(rev_range)
    clean_name = target_tag.lstrip("v")
    lines = [f"## Release {clean_name}", ""]
    lines.extend(build_summary_lines(entries, prev_tag=prev_tag, target_tag=target_tag, rev_range=rev_range))
    lines.extend(["### What's Changed", ""])
    lines.extend(format_entries(entries))

    repo = get_github_repo()
    if prev_tag and repo:
        lines.extend([
            "",
            f"**Full Changelog**: https://github.com/{repo}/compare/{prev_tag}...{target_tag}",
        ])

    lines.extend([
        "",
        "---",
        "📦 **PyPI Package available:** [https://pypi.org/project/gitlab-watcher/](https://pypi.org/project/gitlab-watcher/)",
    ])
    return "\n".join(lines)


def generate_full_changelog():
    """Generate complete CHANGELOG.md content from all tags."""
    tags = get_tags()
    lines = [
        "# Changelog",
        "",
        "All notable changes to the GitLab Watcher project are documented in this file.",
        "",
    ]

    if not tags:
        entries = get_commit_log()
        lines.extend(["## [Unreleased]", ""])
        lines.extend(format_entries(entries))
        lines.append("")
        return "\n".join(lines)

    latest_tag = tags[0]
    unreleased_entries = get_commit_log(f"{latest_tag}..HEAD")
    if unreleased_entries:
        lines.extend(["## [Unreleased]", ""])
        lines.extend(format_entries(unreleased_entries))
        lines.append("")

    for tag in tags:
        tag_date = get_tag_date(tag)
        prev_tag = run_git(["describe", "--tags", "--abbrev=0", f"{tag}^"])
        rev_range = f"{prev_tag}..{tag}" if prev_tag else tag
        entries = get_commit_log(rev_range)
        clean_tag = tag.lstrip("v")
        lines.extend([f"## [{clean_tag}] - {tag_date}", ""])
        lines.extend(format_entries(entries))
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate changelog and release notes from git history.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--release-notes",
        nargs="?",
        const="",
        help="Generate release notes for target tag (default: current tag)",
    )
    group.add_argument("--full", action="store_true", help="Generate complete CHANGELOG.md")

    args = parser.parse_args()

    if args.release_notes is not None:
        target = args.release_notes if args.release_notes != "" else None
        print(generate_release_notes(target))
    elif args.full:
        print(generate_full_changelog())


if __name__ == "__main__":
    main()

