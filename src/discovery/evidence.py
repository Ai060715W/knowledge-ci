from __future__ import annotations

"""Git history evidence for discovery candidates.

Every evidence item is traceable: it carries the commit hash (and short
subject) so a human reviewer can verify the claim. v1 is read-only and works
on any local git repository without platform accounts.
"""

import re
from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.scoring import REVERT_PATTERNS

__all__ = [
    "MAX_COMMITS_PER_SYMBOL",
    "MAX_BLAME_LINES",
    "infer_owners",
    "module_history",
    "symbol_history",
]

#: Cap on history entries returned per symbol/module to keep reports readable.
MAX_COMMITS_PER_SYMBOL = 5

#: Lines sampled from ``git blame`` when inferring owners (the first lines of
#: a file usually carry its original authorship).
MAX_BLAME_LINES = 200


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


_AUTHOR_MAIL_RE = re.compile(r"^author-mail <(.*)>$")
_AUTHOR_NAME_RE = re.compile(r"^author (.*)$")


def _blame_authors(repo: Repo, module_path: str) -> list[dict[str, Any]]:
    """Author line counts for the first lines of ``module_path`` via git blame.

    Returns [{"email", "name", "lines"}] sorted by line count, descending.
    """
    try:
        output = repo.git.blame(
            "--porcelain", "-L", f"1,{MAX_BLAME_LINES}", "--", module_path
        )
    except Exception:
        return []

    counts: dict[str, dict[str, Any]] = {}
    pending_email: str | None = None
    pending_name: str = ""
    for raw_line in output.splitlines():
        if raw_line.startswith("\t"):
            if pending_email:
                entry = counts.setdefault(
                    pending_email, {"email": pending_email, "name": pending_name, "lines": 0}
                )
                entry["lines"] += 1
            continue
        mail_match = _AUTHOR_MAIL_RE.match(raw_line)
        if mail_match:
            pending_email = mail_match.group(1)
            continue
        name_match = _AUTHOR_NAME_RE.match(raw_line)
        if name_match:
            pending_name = name_match.group(1)

    authors = sorted(counts.values(), key=lambda item: (-item["lines"], item["email"]))
    return authors


def infer_owners(
    repo_path: str | Path,
    module_path: str,
    codeowners_path: str | Path | None = None,
) -> dict[str, Any]:
    """Infer suggested owners for a module.

    Resolution order (all values are *suggestions*; confirmation is always a
    human action):

    1. CODEOWNERS entries matching ``module_path`` (explicit file when
       ``codeowners_path`` is given, otherwise the conventional locations
       CODEOWNERS / .github/CODEOWNERS / docs/CODEOWNERS).
    2. ``git blame`` top authors (by line count over the first
       ``MAX_BLAME_LINES`` lines).

    Returns::

        {"codeowners": [...], "blame_authors": [...], "suggested": [...],
         "inferred": true}
    """
    root = Path(repo_path)
    codeowners: list[str] = []
    if codeowners_path is None:
        from src.patch.pr_manager import discover_codeowners

        try:
            codeowners = discover_codeowners(root, [module_path])
        except (OSError, ValueError):
            codeowners = []
    else:
        codeowners = _codeowners_for_path(Path(codeowners_path), module_path)

    repo = None
    blame_authors: list[dict[str, Any]] = []
    try:
        repo = Repo(root)
        blame_authors = _blame_authors(repo, module_path)
    except Exception:
        blame_authors = []
    finally:
        if repo is not None:
            try:
                repo.close()
            except Exception:
                pass

    suggested: list[str] = []
    if codeowners:
        suggested = [entry for entry in codeowners]
    elif blame_authors:
        top = blame_authors[0]
        suggested = [f"{top['name']} <{top['email']}>" if top.get("name") else top["email"]]

    return {
        "codeowners": codeowners,
        "blame_authors": blame_authors,
        "suggested": suggested,
        "inferred": bool(suggested),
    }


def _codeowners_for_path(codeowners_file: Path, module_path: str) -> list[str]:
    """Owners from an explicit CODEOWNERS file matching ``module_path``."""
    from fnmatch import fnmatch

    if not codeowners_file.is_file():
        return []
    owners: set[str] = set()
    for raw_line in codeowners_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, matched_owners = parts[0].lstrip("/"), parts[1:]
        if fnmatch(module_path, pattern) or fnmatch(module_path, f"{pattern}*"):
            owners.update(matched_owners)
    return sorted(owners)
