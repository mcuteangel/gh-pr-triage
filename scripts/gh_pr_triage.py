#!/usr/bin/env python3
"""
GitHub PR Comment Triage Script

Fetches all comments from a GitHub PR (inline code reviews + general conversation),
filters resolved/stale items, tracks state, and outputs structured Markdown.

Auto-detects owner/repo from git remote and PR number from current branch when
positional arguments are omitted. Falls back to explicit arguments outside a repo.

Usage:
    python gh_pr_triage.py [OWNER REPO [PR_NUMBER]] [options]

Options:
    --state-file PATH    Path to state tracking JSON (default: .gh_review_state.json)
    --output PATH        Output Markdown file path (default: stdout)
    --hide-resolved      Completely hide resolved items
    --json               Output raw JSON instead of Markdown
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode


# ---------------------------------------------------------------------------
# Auto-detection helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], check: bool = False) -> str:
    """Run a shell command and return stripped stdout."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Command failed: {cmd}")
    return proc.stdout.strip()


def detect_owner_repo() -> tuple[str, str]:
    """Extract owner/repo from the current git repository's origin remote."""
    url = _run(["git", "remote", "get-url", "origin"])
    if not url:
        raise RuntimeError("No 'origin' remote found")

    # Handle both SSH and HTTPS URLs
    # SSH:   git@github.com:owner/repo.git
    # HTTPS: https://github.com/owner/repo.git
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        raise RuntimeError(f"Cannot parse GitHub owner/repo from remote URL: {url}")

    parts = m.group(1).split("/")
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected remote path: {m.group(1)}")

    return parts[0], parts[1]


def detect_pr_number() -> int:
    """Resolve the PR number for the current branch via `gh pr view`."""
    branch = _run(["git", "branch", "--show-current"])
    if not branch:
        raise RuntimeError("Not on a named branch")

    raw = _run(["gh", "pr", "view", "--json", "number", "--jq", ".number"])
    if not raw or not raw.isdigit():
        raise RuntimeError(f"No PR found for branch '{branch}'")

    return int(raw)


def resolve_repo_args(owner: Optional[str], repo: Optional[str]) -> tuple[str, str]:
    """Return (owner, repo) — from args or auto-detected."""
    if owner and repo:
        return owner, repo
    if (owner and not repo) or (repo and not owner):
        print("Error: provide both OWNER and REPO, or omit both for auto-detection.", file=sys.stderr)
        sys.exit(1)
    try:
        o, r = detect_owner_repo()
        print(f"[auto-detect] repo: {o}/{r}", file=sys.stderr)
        return o, r
    except RuntimeError as e:
        print(f"Error: {e}. Provide OWNER and REPO explicitly.", file=sys.stderr)
        sys.exit(1)


def resolve_pr_number(pr_arg: Optional[str]) -> int:
    """Return PR number — from arg or auto-detected."""
    if pr_arg:
        return int(pr_arg)
    try:
        num = detect_pr_number()
        print(f"[auto-detect] PR: #{num}", file=sys.stderr)
        return num
    except RuntimeError as e:
        print(f"Error: {e}. Provide PR_NUMBER explicitly.", file=sys.stderr)
        sys.exit(1)


class GitHubPRFetcher:
    """Fetches all comments from a GitHub PR with full pagination."""

    def __init__(self, owner: str, repo: str, pr_number: int, token: Optional[str] = None):
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.session_id = f"{owner}/{repo}#{pr_number}"

    def _make_request(self, url: str, params: Optional[Dict] = None) -> Any:
        """Make authenticated GitHub API request."""
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gh-pr-triage/1.0"
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        req = Request(url, headers=headers)

        try:
            with urlopen(req) as response:
                return json.loads(response.read().decode())
        except HTTPError as e:
            if e.code == 401:
                print("Error: Authentication required. Set GITHUB_TOKEN or run 'gh auth login'.", file=sys.stderr)
                sys.exit(1)
            elif e.code == 404:
                print(f"Error: PR not found - {self.owner}/{self.repo}#{self.pr_number}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Error: GitHub API returned {e.code}: {e.reason}", file=sys.stderr)
                sys.exit(1)

    def _paginate(self, url: str, params: Optional[Dict] = None) -> List[Any]:
        """Fetch all pages of results."""
        all_items = []
        if params is None:
            params = {}
        params["per_page"] = 100
        page = 1

        while True:
            params["page"] = page
            items = self._make_request(url, params)

            if not items:
                break

            all_items.extend(items)

            if len(items) < 100:
                break

            page += 1

        return all_items

    def fetch_review_comments(self) -> List[Dict]:
        """Fetch inline code review comments with full pagination."""
        url = f"{self.base_url}/pulls/{self.pr_number}/comments"
        return self._paginate(url)

    def fetch_issue_comments(self) -> List[Dict]:
        """Fetch general conversation comments with full pagination."""
        url = f"{self.base_url}/issues/{self.pr_number}/comments"
        return self._paginate(url)

    def fetch_pr_info(self) -> Dict:
        """Fetch PR metadata."""
        url = f"{self.base_url}/pulls/{self.pr_number}"
        return self._make_request(url)

    def fetch_all(self) -> Tuple[Dict, List[Dict], List[Dict]]:
        """Fetch PR info, review comments, and issue comments."""
        pr_info = self.fetch_pr_info()
        review_comments = self.fetch_review_comments()
        issue_comments = self.fetch_issue_comments()
        return pr_info, review_comments, issue_comments


class CommentProcessor:
    """Processes and filters comments with state tracking."""

    def __init__(self, state_file: str = ".gh_review_state.json"):
        self.state_file = Path(state_file)
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load existing state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"sessions": {}, "processed_comments": {}}
        return {"sessions": {}, "processed_comments": {}}

    def _save_state(self) -> None:
        """Save state to file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def _is_resolved(self, comment: Dict) -> bool:
        """Check if a comment thread is resolved."""
        # GitHub API v3 marks resolved comments with "resolved" field
        if comment.get("resolved"):
            return True
        # Also check for "outdated" diff position
        if comment.get("outdated"):
            return True
        return False

    def _get_comment_id(self, comment: Dict) -> str:
        """Extract unique comment identifier."""
        return str(comment.get("id", ""))

    def _is_new_or_open(self, comment: Dict, session_id: str) -> bool:
        """Check if comment is new or still open."""
        comment_id = self._get_comment_id(comment)
        if comment_id not in self.state.get("processed_comments", {}):
            return True

        stored = self.state["processed_comments"][comment_id]
        # Check if comment was updated after we last saw it
        updated_at = comment.get("updated_at", "")
        if updated_at and updated_at > stored.get("last_seen", ""):
            return True

        # Check if resolved status changed
        if self._is_resolved(comment) != stored.get("was_resolved", False):
            return True

        return False

    def _mark_processed(self, comment: Dict, session_id: str) -> None:
        """Mark a comment as processed."""
        comment_id = self._get_comment_id(comment)
        if "processed_comments" not in self.state:
            self.state["processed_comments"] = {}

        self.state["processed_comments"][comment_id] = {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "was_resolved": self._is_resolved(comment),
            "session": session_id
        }

    def process_comments(
        self,
        review_comments: List[Dict],
        issue_comments: List[Dict],
        session_id: str
    ) -> Dict[str, List[Dict]]:
        """
        Process and categorize comments.

        Returns dict with keys: 'active', 'resolved', 'general'
        """
        result = {"active": [], "resolved": [], "general": []}

        # Process review comments (inline code comments)
        for comment in review_comments:
            comment_id = self._get_comment_id(comment)
            is_resolved = self._is_resolved(comment)
            is_new = self._is_new_or_open(comment, session_id)

            if is_resolved:
                result["resolved"].append({
                    "type": "review",
                    "data": comment,
                    "is_new": is_new
                })
            else:
                result["active"].append({
                    "type": "review",
                    "data": comment,
                    "is_new": is_new
                })

            self._mark_processed(comment, session_id)

        # Process issue comments (general conversation)
        for comment in issue_comments:
            comment_id = self._get_comment_id(comment)
            is_new = self._is_new_or_open(comment, session_id)

            result["general"].append({
                "type": "issue",
                "data": comment,
                "is_new": is_new
            })

            self._mark_processed(comment, session_id)

        # Update session timestamp
        self.state["sessions"][session_id] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "total_review": len(review_comments),
            "total_issue": len(issue_comments)
        }

        self._save_state()
        return result


class MarkdownFormatter:
    """Formats processed comments into structured Markdown with Fix Blocks."""

    DEFAULT_BLOCK_SIZE = 5

    def __init__(self, hide_resolved: bool = False, block_size: int = 5):
        self.hide_resolved = hide_resolved
        self.block_size = max(1, min(block_size, 20))

    # ------------------------------------------------------------------
    # Single-comment renderers
    # ------------------------------------------------------------------

    def _format_comment(self, comment: Dict, comment_type: str = "review") -> str:
        """Format a single comment into Markdown."""
        if comment_type == "review":
            return self._format_review_comment(comment)
        else:
            return self._format_issue_comment(comment)

    def _format_review_comment(self, comment: Dict) -> str:
        """Format an inline review comment."""
        user = comment.get("user", {}).get("login", "unknown")
        body = comment.get("body", "")
        path = comment.get("path", "")
        line = comment.get("original_line") or comment.get("line", "")
        created_at = comment.get("created_at", "")
        is_new = comment.get("is_new", False)

        new_badge = " [NEW]" if is_new else ""
        line_info = f" (line {line})" if line else ""

        lines = [
            f"### {path}{line_info}{new_badge}",
            f"**@{user}** - {created_at}",
            "",
            body,
            ""
        ]
        return "\n".join(lines)

    def _format_issue_comment(self, comment: Dict) -> str:
        """Format a general issue/comment."""
        user = comment.get("user", {}).get("login", "unknown")
        body = comment.get("body", "")
        created_at = comment.get("created_at", "")
        is_new = comment.get("is_new", False)

        new_badge = " [NEW]" if is_new else ""

        lines = [
            f"### Comment by @{user}{new_badge}",
            f"*{created_at}*",
            "",
            body,
            ""
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Grouping and chunking helpers
    # ------------------------------------------------------------------

    def _group_by_file(self, comments: List[Dict]) -> Dict[str, List[Dict]]:
        """Group review comments by file path."""
        grouped: Dict[str, List[Dict]] = {}
        for item in comments:
            comment = item["data"]
            path = comment.get("path", "general")
            grouped.setdefault(path, []).append(item)
        return grouped

    def _flatten_findings(self, grouped: Dict[str, List[Dict]]) -> List[Dict]:
        """Flatten grouped findings into a single list preserving file order."""
        flat: List[Dict] = []
        for path in sorted(grouped):
            for item in grouped[path]:
                flat.append(item)
        return flat

    def _chunk(self, items: List[Dict], size: int) -> List[List[Dict]]:
        """Split items into chunks of `size`."""
        return [items[i:i + size] for i in range(0, len(items), size)]

    # ------------------------------------------------------------------
    # Fix Block instruction generator
    # ------------------------------------------------------------------

    def _fix_block_instruction(
        self, block_index: int, total_blocks: int, findings_in_block: int
    ) -> str:
        """Generate the LLM instruction footer for a Fix Block."""
        if block_index < total_blocks:
            next_action = (
                f"After resolving these {findings_in_block} finding(s), "
                f"read Fix Block {block_index + 1}/{total_blocks} "
                f"and repeat the process."
            )
        else:
            next_action = (
                "This is the final Fix Block. "
                "After resolving these findings, generate a summary of all changes made."
            )

        return (
            f"<!-- FIX_BLOCK {block_index}/{total_blocks} "
            f"| findings={findings_in_block} -->\n"
            f"---\n"
            f"**[LLM INSTRUCTION — Fix Block {block_index}/{total_blocks}]**\n\n"
            f"Process the {findings_in_block} finding(s) above in this block. "
            f"For each finding:\n"
            f"1. Read the linked file and line number.\n"
            f"2. Understand the reviewer's concern.\n"
            f"3. Apply the fix directly to the codebase.\n"
            f"4. Verify the fix compiles/passes tests.\n"
            f"5. Mark the finding as resolved in your tracking.\n\n"
            f"{next_action}\n\n"
            f"<!-- END_FIX_BLOCK -->\n"
        )

    # ------------------------------------------------------------------
    # Main format method
    # ------------------------------------------------------------------

    def format(
        self,
        pr_info: Dict,
        categorized: Dict[str, List[Dict]]
    ) -> str:
        """Format all comments into structured Markdown with Fix Blocks."""
        lines: List[str] = []

        # Header
        title = pr_info.get("title", "Unknown PR")
        number = pr_info.get("number", "?")
        state = pr_info.get("state", "unknown")
        url = pr_info.get("html_url", "")

        lines.extend([
            f"# PR Triage: {title}",
            f"**#{number}** [{state}]({url})",
            "",
            f"*Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
        ])

        # Active findings — chunked into Fix Blocks
        active = categorized.get("active", [])
        if active:
            grouped = self._group_by_file(active)
            flat = self._flatten_findings(grouped)
            blocks = self._chunk(flat, self.block_size)
            total_blocks = len(blocks)

            lines.extend([
                "## Findings",
                "",
                f"**{len(active)} active finding(s)** across "
                f"{len(grouped)} file(s), split into **{total_blocks} Fix Block(s)** "
                f"of up to {self.block_size} findings each.",
                "",
                "Process each block sequentially. Do NOT skip ahead.",
                "",
                "---",
                "",
            ])

            for idx, block in enumerate(blocks, start=1):
                lines.append(f"## Fix Block {idx}/{total_blocks}")
                lines.append("")

                # Group block findings by file for context
                block_by_file: Dict[str, List[Dict]] = {}
                for item in block:
                    fpath = item["data"].get("path", "general")
                    block_by_file.setdefault(fpath, []).append(item)

                for fpath, fitems in sorted(block_by_file.items()):
                    lines.append(f"### `{fpath}`")
                    lines.append("")
                    for item in fitems:
                        lines.append(self._format_comment(item["data"], "review"))

                # Inject LLM instruction
                lines.append(self._fix_block_instruction(idx, total_blocks, len(block)))
                lines.append("")

        # General discussions
        general = categorized.get("general", [])
        if general:
            lines.extend([
                "## General Discussions",
                ""
            ])
            for item in general:
                lines.append(self._format_comment(item["data"], "issue"))
            lines.append("")

        # Resolved items
        resolved = categorized.get("resolved", [])
        if resolved and not self.hide_resolved:
            lines.extend([
                "## Resolved/Filtered Items",
                f"*{len(resolved)} resolved comments filtered*",
                ""
            ])
            for item in resolved[:5]:
                comment = item["data"]
                path = comment.get("path", "")
                line = comment.get("original_line") or comment.get("line", "")
                user = comment.get("user", {}).get("login", "unknown")
                lines.append(f"- `{path}:{line}` by @{user}")
            if len(resolved) > 5:
                lines.append(f"- ... and {len(resolved) - 5} more")
            lines.append("")

        # Summary
        lines.extend([
            "---",
            "",
            "## Summary",
            "",
            f"- **Active comments:** {len(active)}",
            f"- **General discussions:** {len(general)}",
            f"- **Resolved (filtered):** {len(resolved)}",
            ""
        ])

        return "\n".join(lines)


def main():
    # Force UTF-8 stdout on Windows (charmap can't encode emoji)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Fetch and triage GitHub PR comments"
    )
    parser.add_argument("owner", nargs="?", default=None, help="Repository owner (auto-detected from git remote)")
    parser.add_argument("repo", nargs="?", default=None, help="Repository name (auto-detected from git remote)")
    parser.add_argument("pr_number", nargs="?", default=None, help="PR number (auto-detected from current branch)")
    parser.add_argument(
        "--state-file",
        default=".gh_review_state.json",
        help="Path to state tracking JSON (default: .gh_review_state.json)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--hide-resolved",
        action="store_true",
        help="Completely hide resolved items"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of Markdown"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        metavar="N",
        help="Findings per Fix Block (1-20, default: 5)"
    )

    args = parser.parse_args()

    # Resolve arguments — auto-detect if not provided
    owner, repo = resolve_repo_args(args.owner, args.repo)
    pr_number = resolve_pr_number(args.pr_number)

    # Fetch comments
    fetcher = GitHubPRFetcher(owner, repo, pr_number)
    pr_info, review_comments, issue_comments = fetcher.fetch_all()

    # Process and filter
    session_id = f"{owner}/{repo}#{pr_number}"
    processor = CommentProcessor(args.state_file)
    categorized = processor.process_comments(review_comments, issue_comments, session_id)

    # Format output
    if args.json:
        output = json.dumps({
            "pr": {
                "number": pr_info.get("number"),
                "title": pr_info.get("title"),
                "state": pr_info.get("state"),
                "url": pr_info.get("html_url")
            },
            "categorized": {
                "active": [item["data"] for item in categorized["active"]],
                "general": [item["data"] for item in categorized["general"]],
                "resolved": [item["data"] for item in categorized["resolved"]]
            },
            "summary": {
                "active_count": len(categorized["active"]),
                "general_count": len(categorized["general"]),
                "resolved_count": len(categorized["resolved"])
            }
        }, indent=2)
    else:
        formatter = MarkdownFormatter(args.hide_resolved, block_size=args.batch_size)
        output = formatter.format(pr_info, categorized)

    # Write output
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
