---
name: gh-pr-triage
description: >
  Fetch and triage GitHub Pull Request comments intelligently with full pagination,
  differential scanning, and state tracking. Use when the user provides a GitHub PR
  context (URL, owner/repo/number) and wants to understand review comments, filter
  resolved items, or get a structured summary of feedback. Triggers on mentions of
  "PR comments", "review feedback", "triage PR", "fetch PR comments", or when a
  GitHub PR URL is provided.
---

# GitHub PR Comment Triage

Fetch all comments from a GitHub PR (both inline code reviews and general conversation),
filter resolved/stale items, and produce a structured Markdown summary.

## Quick Start — Zero Arguments

Inside any git repo with an open PR on the current branch:

```bash
python scripts/gh_pr_triage.py
```

That's it. The script auto-detects:
- **Owner/Repo** from `git remote get-url origin`
- **PR number** from `gh pr view` on the current branch

## Explicit Arguments (fallback)

When running outside a repo or targeting a different PR:

```bash
python scripts/gh_pr_triage.py <OWNER> <REPO> <PR_NUMBER>
```

All three positional arguments are optional — provide none (auto-detect), all three (explicit), or any mismatch produces a clear error.

## Features

1. **Zero-Argument Mode** - Auto-detects owner/repo/PR from git context
2. **Full Pagination** - Loops until empty, guaranteed 100% data retrieval
3. **Dual Comment Sources** - Inline code review comments + general conversation comments
4. **Smart Filtering** - Marks resolved comments as `[RESOLVED]`, detects stale diff hunks
5. **State Tracking** - Maintains `.gh_review_state.json` to show only NEW or STILL OPEN items on subsequent runs
6. **Structured Output** - Markdown grouped by severity with file/line context

## Output Format

The script produces a clean Markdown summary with these sections:

- 🚨 **New/Active Critical/High Findings** - Grouped by file and line number
- 💬 **General Discussions/Feedback** - Questions, suggestions, general comments
- ✅ **Auto-Filtered/Resolved Items** - Brief summary only (or hidden with --hide-resolved)

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `--state-file` | Path to state tracking JSON | `.gh_review_state.json` |
| `--output` | Output Markdown file path | stdout |
| `--hide-resolved` | Completely hide resolved items | false |
| `--json` | Output raw JSON instead of Markdown | false |

## Auto-Detection Logic

When positional args are omitted:

1. Runs `git remote get-url origin` → parses `github.com:owner/repo` or `github.com/owner/repo`
2. Runs `git branch --show-current` → gets current branch name
3. Runs `gh pr view --json number --jq .number` → resolves PR number for that branch

Falls back to explicit args if any step fails (not in a repo, no origin remote, no PR for branch).

## Requirements

- Python 3.8+
- `gh` CLI authenticated (`gh auth login`) or `GITHUB_TOKEN` env var
- Must be inside a git repo for auto-detection (or provide explicit args)
