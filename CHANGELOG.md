# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added (Plan 0: engineering & schema foundation)

- `pyproject.toml` with the unified `kc` CLI (`kc init/analyze/generate/apply/inject/feedback/check-llm/migrate`); legacy `scripts/*.py` entry points remain compatible wrappers.
- Knowledge unit schema v2 (`src/registry/schema.py` + `schema_spec.py`): `title/summary/rationale`, symbol-level `scope`, `evidence`, `confidence`, `owner/reviewer`, and the status state machine (`proposed → under_review → active → outdated → retired`); jsonschema validation; only `active` knowledge is injected.
- `scripts/migrate_registry.py` (`kc migrate`): v1→v2 registry migration with `--dry-run`, automatic `.v1.bak` backup, `--rollback`, and idempotent re-runs.
- `src/registry/store.py`: stable read/write interface with atomic JSON writes and `evidence`/`metrics` data directories.
- Symbol-level unit matching (`scope.symbols`) as a fallback to file glob matching.
- Config sections `discovery` / `freshness` / `owners` with defaults (`load_settings`), plus `evidence_path`/`metrics_path`.
- 54 new unit tests (schema, state machine, migration, store, CLI) — 87 total, all passing.

### Added (Plan 1: hidden knowledge discovery MVP)

- `src/discovery/depgraph.py`: stdlib-`ast` dependency graph (import/use/inherit edges, module + symbol level, degree centrality, cross-layer impact, Tarjan import cycles, per-commit JSON cache, graceful syntax-error skipping).
- `src/discovery/scoring.py`: design-doc formula `α·改动频率 + β·依赖中心性 + γ·事故 + δ·回滚 + ε·贡献者熵 + ζ·跨层影响` with configurable weights, one-pass `git log` history stats.
- `src/discovery/signals.py`: magic numbers, module-level global instances, compatibility/bridge layers, long functions/classes, dependency cycles, revert history.
- `src/discovery/evidence.py`: traceable git evidence (introduced/modified/reverted commits, `-G` diff search per symbol).
- `kc discover` / `scripts/discover.py`: read-only, LLM-free analysis of any repository (`--repo`); writes a JSON report with Top-K modules, candidate drafts (`status: proposed`, `confidence: null`), and bilingual owner questions. Graceful degradation for non-git dirs and repos without Python files; `discovery.exclude_paths` config.
- 35 new hermetic tests — 122 total, all passing.
- Validated on `psf/requests` (@80683562): 37 modules scanned, Top-10 + 24 candidates + 47 questions; `exclude_paths: [tests]` re-ranking verified.

## [0.1.0] - 2026-08-17

Initial open-source release, generalized from the 4-week Knowledge CI POC.

### Added

- `init_project.py`: one-command onboarding that creates `.knowledge-ci/` for any project.
- `analyze_commit.py`: commit impact analysis (changed files, symbol summaries, affected units, unmanaged files, related-doc suggestions).
- `generate_patch.py`: LLM knowledge patch generation with Delta validation, fuzzy-word checks, self-correction retries (up to 3), `--mock-response-file` offline mode, and `--review-feedback` correction mode.
- `apply_patch.py`: lands reviewed patches into `registry.json` and marks them APPLIED.
- `inject_context.py`: pre-edit context injection (knowledge summary, risk level, historical decisions, impact warnings, last verified), 500-token budget, `--json`/`--max-tokens`/`--verbose`.
- `feedback_server.py`: stdlib server hosting the Quill patch preview and collecting JSONL feedback.
- Quill static previewer with before/after Delta comparison.
- Cursor rules and VS Code task templates.
- Bilingual README, quickstart, config, and architecture docs.
- `example/` mini payment project with sample knowledge units.
- 33 unit tests covering matcher, impact analysis, patch phase, and injection.
