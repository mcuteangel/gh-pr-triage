# gh-pr-triage

A MimoCode/OpenCode skill that fetches, filters, and triages GitHub Pull Request comments with full pagination, resolved/stale detection, and stateful differential scanning.

## What It Does

- Fetches **all** PR comments (inline code reviews + conversation) with guaranteed 100% pagination
- Auto-detects owner/repo from `git remote` and PR number from current branch — zero arguments needed
- Filters resolved and stale threads via GitHub's GraphQL API
- Tracks state across runs so you only see **new** or **still-open** findings
- Outputs categorized Markdown with severity badges (critical/high/medium/low)

## Install

Clone this repo directly into your MimoCode or OpenCode skills directory:

```bash
# MimoCode
git clone https://github.com/mcuteangel/gh-pr-triage.git ~/.local/share/mimocode/skills/gh-pr-triage

# OpenCode / Codex
git clone https://github.com/mcuteangel/gh-pr-triage.git ~/.opencode/skills/gh-pr-triage
```

No dependencies beyond Python 3.8+ and an authenticated `gh` CLI.

## Usage

### Zero Arguments (recommended)

Inside any git repo with an open PR on the current branch:

```bash
python scripts/gh_pr_triage.py
```

The script auto-detects everything from your git context.

### Explicit Arguments

```bash
python scripts/gh_pr_triage.py <OWNER> <REPO> <PR_NUMBER>
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--state-file PATH` | Path to state tracking JSON | `.gh_review_state.json` |
| `--output PATH` | Output file path | stdout |
| `--hide-resolved` | Hide resolved items completely | false |
| `--json` | Output raw JSON instead of Markdown | false |
| `--fresh` | Ignore state file, treat all as new | false |

## How Auto-Detection Works

1. `git remote get-url origin` → parses owner/repo
2. `git branch --show-current` → gets current branch
3. `gh pr view --json number --jq .number` → resolves PR number

Falls back to explicit arguments if any step fails.

## State Tracking

The script writes `.gh_review_state.json` in your project root. On subsequent runs, only previously unseen comments are highlighted as "new". Delete the file or use `--fresh` to reset.

## Output Example

```markdown
# PR Triage: Improve release create behavior
**#9385** [closed](https://github.com/cli/cli/pull/9385)

## 🚨 New/Active Findings

### 📄 `pkg/cmd/release/create/create.go`

### pkg/cmd/release/create/create.go (line 524)
**@andyfeller** - 2024-08-06T11:46:16Z

Updating this function to return empty string for the body if an error
is raised makes sense. That said, I wonder if that was always the case.

## 💬 General Discussions
...

## Summary
- Active comments: 3
- General discussions: 1
- Resolved (filtered): 5
```

## License

MIT
