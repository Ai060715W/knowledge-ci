from __future__ import annotations

"""Git history evidence for discovery candidates.

Every evidence item is traceable: it carries the commit hash (and short
subject) so a human reviewer can verify the claim. v1 is read-only and works
on any local git repository without platform accounts.
"""

from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.scoring import REVERT_PATTERNS

__all__ = ["MAX_COMMITS_PER_SYMBOL", "module_history", "symbol_history"]

#: Cap on history entries returned per symbol/module to keep reports readable.
MAX_COMMITS_PER_SYMBOL = 5


def _format_commit(commit: Any, role: str) -> dict[str, Any]:
    subject = (commit.summary or "").strip()
    return {
        "type": "commit",
        "id": commit.hexsha,
        "short_id": commit.hexsha[:8],
        "subject": subject[:120],
        "author": commit.author.email if commit.author else "",
        "role": role,
    }


def _is_revert(subject: str) -> bool:
    lowered = subject.lower()
    return any(keyword in lowered for keyword in REVERT_PATTERNS)


def module_history(repo_path: str | Path, module_path: str, max_commits: int = MAX_COMMITS_PER_SYMBOL) -> list[dict[str, Any]]:
    """Commits that touched ``module_path`` (newest first, capped).

    The oldest entry (when history is long) is labelled ``introduced``.
    Returns [] for non-git directories or unknown paths.
    """
    repo = None
    try:
        repo = Repo(repo_path)
        commits = list(repo.iter_commits(paths=module_path, max_count=200))
    except Exception:
        return []
    finally:
        if repo is not None:
            try:
                repo.close()
            except Exception:
                pass

    if not commits:
        return []

    evidence: list[dict[str, Any]] = []
    for commit in commits[:max_commits]:
        subject = (commit.summary or "").strip()
        role = "reverted" if _is_revert(subject) else "modified"
        evidence.append(_format_commit(commit, role))
    if len(commits) > max_commits:
        oldest = commits[-1]
        evidence.append(_format_commit(oldest, "introduced"))
    elif commits:
        evidence[-1]["role"] = "introduced"
    return evidence


def symbol_history(
    repo_path: str | Path,
    module_path: str,
    symbol: str,
    max_commits: int = MAX_COMMITS_PER_SYMBOL,
) -> list[dict[str, Any]]:
    """Commits whose diff added/removed lines mentioning ``symbol``.

    Uses ``git log -G`` (diff regex) so content edits are caught even when the
    occurrence count stays the same. Returns [] when git is unavailable or the
    symbol never appeared in a diff.
    """
    repo = None
    try:
        repo = Repo(repo_path)
        raw_hashes = repo.git.log("--format=%H", "-G", symbol, "--", module_path).splitlines()
        commits = [repo.commit(hash_line) for hash_line in raw_hashes[:50] if hash_line]
    except Exception:
        return []
    finally:
        if repo is not None:
            try:
                repo.close()
            except Exception:
                pass

    evidence: list[dict[str, Any]] = []
    for commit in commits[:max_commits]:
        subject = (commit.summary or "").strip()
        role = "reverted" if _is_revert(subject) else "modified"
        evidence.append(_format_commit(commit, role))
    if len(commits) > max_commits:
        evidence.append(_format_commit(commits[-1], "introduced"))
    return evidence
