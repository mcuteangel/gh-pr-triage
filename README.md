# gh-pr-triage

A MimoCode/OpenCode skill that fetches, filters, and triages GitHub Pull Request comments with full pagination, resolved/stale detection, and stateful differential scanning.

## What It Does

- Fetches **all** PR comments (inline code reviews + conversation) with guaranteed 100% pagination
- Auto-detects owner/repo from `git remote` and PR number from current branch — zero arguments needed
- Filters resolved and stale threads via GitHub's GraphQL API
- Tracks state across runs so you only see **new** or **still-open** findings
- Outputs categorized Markdown with severity badges (critical/high/medium/low)

## Prerequisites

- Python 3.8+
- Git
- GitHub CLI (`gh`) authenticated — run `gh auth login`

## Install

Clone this repo into your agent's skills directory:

### MimoCode

**Linux / macOS:**
```bash
git clone https://github.com/mcuteangel/gh-pr-triage.git ~/.agents/skills/gh-pr-triage
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/mcuteangel/gh-pr-triage.git "$env:USERPROFILE\.agents\skills\gh-pr-triage"
```

### OpenCode / Codex

**Linux / macOS:**
```bash
git clone https://github.com/mcuteangel/gh-pr-triage.git ~/.opencode/skills/gh-pr-triage
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/mcuteangel/gh-pr-triage.git "$env:USERPROFILE\.opencode\skills\gh-pr-triage"
```

### Standalone (no agent)

Clone anywhere and run the script directly:

**Linux / macOS:**
```bash
git clone https://github.com/mcuteangel/gh-pr-triage.git ~/gh-pr-triage
cd ~/gh-pr-triage
python3 scripts/gh_pr_triage.py
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/mcuteangel/gh-pr-triage.git "$env:USERPROFILE\gh-pr-triage"
cd "$env:USERPROFILE\gh-pr-triage"
python scripts/gh_pr_triage.py
```

## Usage

### Zero Arguments (recommended)

Inside any git repo with an open PR on the current branch:

**Linux / macOS:**
```bash
python3 scripts/gh_pr_triage.py
```

**Windows (PowerShell):**
```powershell
python scripts/gh_pr_triage.py
```

The script auto-detects everything from your git context.

### Explicit Arguments

Provide owner, repo, and PR number manually:

**Linux / macOS:**
```bash
python3 scripts/gh_pr_triage.py <OWNER> <REPO> <PR_NUMBER>
```

**Windows (PowerShell):**
```powershell
python scripts/gh_pr_triage.py <OWNER> <REPO> <PR_NUMBER>
```

### All CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--state-file PATH` | Path to state tracking JSON | `.gh_review_state.json` |
| `--output PATH` | Write output to file instead of stdout | stdout |
| `--hide-resolved` | Completely hide resolved items | false |
| `--json` | Output raw JSON instead of Markdown | false |
| `--fresh` | Ignore state file, treat all comments as new | false |
| `--batch-size N` | Findings per Fix Block (1-20) | 5 |

### Full Examples

**Triage current branch's PR (auto-detect):**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py
```

Windows:
```powershell
python scripts/gh_pr_triage.py
```

**Triage a specific PR:**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py octocat Hello-World 42
```

Windows:
```powershell
python scripts/gh_pr_triage.py octocat Hello-World 42
```

**Include resolved items in output:**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --hide-resolved false
```

Windows:
```powershell
python scripts/gh_pr_triage.py --hide-resolved false
```

**Output as JSON (for programmatic use):**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --json
```

Windows:
```powershell
python scripts/gh_pr_triage.py --json
```

**Write output to a file:**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --output review.md
```

Windows:
```powershell
python scripts/gh_pr_triage.py --output review.md
```

**Fresh run (ignore previous state):**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --fresh
```

Windows:
```powershell
python scripts/gh_pr_triage.py --fresh
```

**Custom state file location:**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --state-file /tmp/my_state.json
```

Windows:
```powershell
python scripts/gh_pr_triage.py --state-file "$env:TEMP\my_state.json"
```

**Combined flags:**

Linux / macOS:
```bash
python3 scripts/gh_pr_triage.py --fresh --json --output results.json
```

Windows:
```powershell
python scripts/gh_pr_triage.py --fresh --json --output results.json
```

## How Auto-Detection Works

When positional arguments are omitted:

1. `git remote get-url origin` — parses `github.com:owner/repo` or `github.com/owner/repo`
2. `git branch --show-current` — gets current branch name
3. `gh pr view --json number --jq .number` — resolves PR number for that branch

Falls back to explicit arguments if any step fails (not in a repo, no origin remote, no PR for branch).

## Fix Blocks (Iterative Processing)

The output is structured into **Fix Blocks** — discrete chunks of findings (default: 5 per block) with embedded LLM instructions. This prevents context window saturation on large PRs.

Each block contains:
- Up to `--batch-size` findings (grouped by file)
- A machine-parseable `<!-- FIX_BLOCK N/M -->` marker
- A step-by-step instruction block telling the LLM how to process the findings
- A pointer to the next block (or a "generate summary" instruction for the final block)

**Example with `--batch-size 3`:**
```
## Fix Block 1/3
  └─ 3 findings...
  └─ [LLM INSTRUCTION] → "read Fix Block 2/3 and repeat"

## Fix Block 2/3
  └─ 3 findings...
  └─ [LLM INSTRUCTION] → "read Fix Block 3/3 and repeat"

## Fix Block 3/3
  └─ 1 finding (final)
  └─ [LLM INSTRUCTION] → "generate a summary of all changes made"
```

Adjust `--batch-size` based on your context window: smaller blocks (2-3) for tighter budgets, larger (7-10) for generous ones.

## State Tracking

The script writes `.gh_review_state.json` in your project root. On subsequent runs, only previously unseen comments are highlighted as "new". Delete the file or use `--fresh` to reset.

**Reset state:**

Linux / macOS:
```bash
rm .gh_review_state.json
```

Windows:
```powershell
Remove-Item .gh_review_state.json
```

## Output Example

```markdown
# PR Triage: Improve release create behavior
**#9385** [closed](https://github.com/cli/cli/pull/9385)

## New/Active Findings

### pkg/cmd/release/create/create.go (line 524)
**@andyfeller** - 2024-08-06T11:46:16Z

Updating this function to return empty string for the body if an error
is raised makes sense. That said, I wonder if that was always the case.

## General Discussions
...

## Summary
- Active comments: 3
- General discussions: 1
- Resolved (filtered): 5
```

## License

MIT
