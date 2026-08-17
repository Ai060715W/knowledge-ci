from __future__ import annotations

"""Owner questions for discovery candidates (local file loop, no platform).

Two actions:

- ``questions``: turn a discovery report into ``questions_<ts>.json``.
- ``answer``: record a human answer back into the questions document; with
  ``--confirm`` the candidate is landed into the registry as
  ``status: under_review`` (the existing review pipeline takes over from
  there), its evidence chain gains a ``human_answer`` item, and confidence is
  recomputed.

Owner values produced here are still suggestions until a human confirms them;
an explicit ``--owner`` always wins over the inferred one.
"""

import argparse
import json
from pathlib import Path

from src.config import load_project_paths, load_settings, resolve_config_path
from src.evidence.confidence import compute_confidence
from src.evidence.questions import (
    QuestionDocError,
    add_answer,
    save_questions_document,
    write_questions,
)
from src.registry.schema import validate_unit
from src.registry.store import RegistryStore


HELP = "Ask/answer owner questions for discovery candidates."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate owner questions from a discovery report, or record human answers.",
        add_help=add_help,
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["questions", "answer"],
        help="questions: write a questions_<ts>.json from a discovery report; "
        "answer: record an answer (and optionally land the candidate).",
    )
    parser.add_argument("--report", default=None, help="Path to a discovery_<ts>.json report.")
    parser.add_argument("--questions", default=None, help="Path to a questions_<ts>.json document.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for the generated questions file (default: the report's directory).",
    )
    parser.add_argument("--candidate", default=None, help="Candidate id to answer (answer action).")
    parser.add_argument("--answer", default=None, help="Human answer text (answer action).")
    parser.add_argument(
        "--owner",
        default=None,
        help="Confirmed owner name (answer action; overrides the inferred suggestion).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Land the candidate into the registry as under_review (answer action).",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="registry.json to write on --confirm (overrides the --config derived path).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    return parser


def _load_json(path: Path, label: str) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise QuestionDocError(f"Cannot read {label} {path}: {error}") from error


def _run_questions(args: argparse.Namespace) -> int:
    if not args.report:
        print("--report <discovery_<ts>.json> is required for action=questions.")
        return 1
    report_path = Path(args.report).resolve()
    report = _load_json(report_path, "discovery report")
    candidates = report.get("candidates") or []
    if not candidates:
        print("The discovery report contains no candidates; nothing to ask about.")
        return 0

    out_dir = Path(args.out) if args.out else report_path.parent
    document, output_path = write_questions(candidates, report.get("repo", ""), out_dir)

    total_questions = sum(len(entry["items"]) for entry in document["questions"])
    insufficient = sum(1 for entry in document["questions"] if entry["insufficient"])
    print(f"Wrote questions: {output_path}")
    print(f"Candidates: {len(document['questions'])}")
    print(f"Questions: {total_questions}")
    print(f"Insufficient evidence (extra questions asked): {insufficient}")
    return 0


def _resolve_registry_path(args: argparse.Namespace) -> Path:
    if args.registry:
        return Path(args.registry).resolve()
    try:
        paths = load_project_paths(resolve_config_path(args.config))
    except SystemExit as error:
        raise QuestionDocError(
            "--confirm needs --registry <path>, or a configured project (kc init first)."
        ) from error
    return Path(paths["registry_path"])


def _land_candidate(
    candidate: dict[str, Any],
    answer: str,
    owner: str | None,
    registry_path: Path,
    settings: dict | None,
) -> tuple[dict[str, Any], bool]:
    """Build/update the registry unit for a confirmed candidate.

    Returns (unit, created). An existing unit keeps its status and only gains
    the answer, owner, and recomputed confidence.
    """
    evidence = list(candidate.get("evidence") or [])
    evidence.append(
        {
            "type": "human_answer",
            "id": str(candidate.get("id", "")),
            "note": answer[:300],
        }
    )

    resolved_owner = owner or candidate.get("owner")
    owner_inferred = resolved_owner is None or (owner is None)

    unit: dict[str, Any] = {
        "id": candidate.get("id", ""),
        "title": candidate.get("title", ""),
        "summary": candidate.get("summary", ""),
        "rationale": candidate.get("rationale", ""),
        "scope": candidate.get("scope", {"files": [], "symbols": []}),
        "evidence": evidence,
        "confidence": compute_confidence(evidence, settings),
        "owner": resolved_owner,
        "owner_inferred": owner_inferred,
        "reviewer": None,
        "status": "under_review",
        "knowledge_delta": candidate.get("knowledge_delta", {"ops": []}),
        "related_docs": candidate.get("related_docs", []),
        "last_verified": candidate.get("last_verified"),
        "code_hash": candidate.get("code_hash", ""),
        "version": int(candidate.get("version", 1)),
    }

    store = RegistryStore(registry_path=registry_path)
    if not registry_path.is_file():
        raise QuestionDocError(
            f"Registry not found: {registry_path}. Run kc init in the project first, "
            "or pass --registry explicitly."
        )

    existing = store.find_unit(unit["id"])
    if existing is not None:
        existing["evidence"] = evidence
        existing["confidence"] = unit["confidence"]
        existing["owner"] = unit["owner"]
        existing["owner_inferred"] = unit["owner_inferred"]
        existing["reviewer"] = existing.get("reviewer")
        store.upsert_unit(existing)
        return existing, False

    errors = validate_unit(unit)
    if errors:
        raise QuestionDocError("Candidate draft is not a valid v2 unit:\n- " + "\n- ".join(errors))
    store.upsert_unit(unit)
    return unit, True


def _run_answer(args: argparse.Namespace) -> int:
    if not args.questions:
        print("--questions <questions_<ts>.json> is required for action=answer.")
        return 1
    if not args.candidate:
        print("--candidate <id> is required for action=answer.")
        return 1
    if not args.answer or not args.answer.strip():
        print("--answer <text> is required for action=answer.")
        return 1

    questions_path = Path(args.questions).resolve()
    document = _load_json(questions_path, "questions document")
    try:
        add_answer(document, args.candidate, args.answer.strip(), owner=args.owner)
    except QuestionDocError as error:
        print(f"Error: {error}")
        return 1
    save_questions_document(document, questions_path)
    print(f"Answer recorded for {args.candidate} in {questions_path}")

    if not args.confirm:
        print("Tip: re-run with --confirm to land the candidate into the registry.")
        return 0

    if not args.report:
        print("--report <discovery_<ts>.json> is required together with --confirm.")
        return 1
    report = _load_json(Path(args.report).resolve(), "discovery report")
    candidate = next(
        (item for item in report.get("candidates", []) if item.get("id") == args.candidate),
        None,
    )
    if candidate is None:
        print(f"Error: candidate {args.candidate} not found in the discovery report.")
        return 1

    settings = None
    if args.config:
        settings = load_settings(resolve_config_path(args.config))
    try:
        registry_path = _resolve_registry_path(args)
        unit, created = _land_candidate(
            candidate, args.answer.strip(), args.owner, registry_path, settings
        )
    except QuestionDocError as error:
        print(f"Error: {error}")
        return 1

    if created:
        print(f"Landed {unit['id']} into {registry_path} (status -> under_review).")
    else:
        print(f"Updated existing unit {unit['id']} in {registry_path} (status kept).")
    print(f"Confidence: {unit['confidence']}")
    print(f"Owner: {unit['owner'] or '(none)'}" + (" [inferred]" if unit.get("owner_inferred") else " [confirmed]"))
    print("Next: review the unit (kc generate / kc apply) to move it toward active.")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.action == "questions":
        return _run_questions(args)
    return _run_answer(args)


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
