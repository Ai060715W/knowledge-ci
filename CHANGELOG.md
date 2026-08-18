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

### Added (Plan 2: evidence chain aggregation & question loop)

- `src/evidence/confidence.py`: documented confidence formula — noisy-OR over distinct evidence types, weighted `human_answer 0.9 > incident 0.6 > mr 0.5 > issue 0.4 > commit 0.3 > code 0.2` (configurable via `discovery.confidence_weights`); reproduces the design document's `0.93` example (commit + incident + human answer).
- `src/evidence/questions.py`: bilingual question templates per signal kind, insufficiency detection (thin evidence chains get extra "where is the source / who owns this" questions), `questions_<ts>.json` documents with answer history.
- `src/discovery/evidence.py` extension: owner inference — CODEOWNERS first (explicit path or conventional locations), `git blame` fallback (top author by line count); owners are always suggestions (`owner_inferred: true`) until a human confirms.
- `kc ask-owner` / `scripts/ask_owner.py`: `--action questions` turns a discovery report into a questions file; `--action answer` records human answers and, with `--confirm`, lands the candidate into the registry as `status: under_review` (existing review pipeline takes over), adds a `human_answer` evidence item, and recomputes confidence.
- Discovery reports now carry per-module `owners` inference and per-candidate `confidence`/`signal_kind`.
- 32 new hermetic tests — 154 total, all passing.
- Validated end-to-end on `psf/requests`: discover → questions → answer/confirm landed a candidate as `under_review` with confidence `0.93` (matching the design example) and owner inference correctly pointing at the module's original author.

### Added (Plan 3: knowledge freshness — four-layer funnel)

- `src/freshness/layers.py`: layer 1 time anchor (`code_hash` → `last_verified` → `time_filter_days` fallback; missing files short-circuit to outdated), layer 2 normalized-AST semantic filter (comments/formatting/docstrings/import reordering/unused-local renames are noise; parse failures are conservative), layer 3 dependency impact (direct scope hits with symbol intersection, indirect hits within `indirect_depth` edges of the dependency graph, either direction).
- `src/freshness/llm.py`: layer 4 strict-JSON verdict (`still_valid`/`partial_update`/`outdated`/`new_knowledge`) with jsonschema validation, Delta-op validation for `partial_update`, fuzzy-word rejection, self-correction retries (≤3), and offline `--mock-response-file` mode.
- `src/freshness/check.py` + `kc freshness`: read-only orchestrator by default; `--apply` only refreshes `last_verified`/`code_hash` and performs state-machine transitions (`active → outdated`); `--auto-patch` turns `partial_update` verdicts into PENDING patch files (human review still required); `--no-llm` and missing API keys degrade to `needs_llm`; `llm_max_units` caps per-run cost.
- Every unit's result carries the full per-layer decision log (reasons, commits, per-file AST verdicts, dependency hits) in `freshness_<ts>.json`; `new_knowledge` drafts are exposed as `candidates` so `kc ask-owner` continues the loop.
- Config: `freshness.indirect_depth` and `freshness.llm_max_units`.
- 35 new hermetic tests — 189 total, all passing.
- Validated on `psf/requests` (@80683562) with handcrafted units: all four verdicts hit; a comment-only commit was correctly filtered by layer 2 (`ast_noise`); a real 2024-era adapters unit flowed through 15 commits to the LLM layer with per-commit AST evidence; `--apply` state transitions, PENDING patch generation, and the ask-owner hand-off were verified end-to-end.

### Added (Plan 4: event triggers & observability metrics)

- `src/webhook/`: stdlib `http.server` webhook server (`POST /webhook/push`, `POST /webhook/mr`, `GET /health`) with GitHub `X-Hub-Signature-256` verification (timing-safe; the CLI refuses to start without a secret unless `--insecure`), GitHub push/pull_request parsing behind a platform-adapter extension point, and an event pipeline that runs analyze/freshness/discover **write-only** — artifacts land in `data/reports`/`data/patches` and the registry is never modified.
- `src/metrics/` + `kc metrics`: the four design-document KPIs with explicit formulas, numerators/denominators, and notes — `coverage` (Top-K modules with active knowledge), `freshness_rate` (reuses the layer-1 time anchor), `hit_rate` (feedback records with the new `adopted` flag), `confirmation_rate` (APPLIED / (APPLIED+REJECTED) patches, with registry status distribution for context); missing inputs degrade to `null` with a note, never a crash.
- Feedback endpoint accepts `adopted=true/false`; `record_feedback` gains an `adopted` parameter.
- Config: `webhook` section (secret, bind host/port, per-event actions, `repos` mapping, `auto_patch`).
- Fix: Python files with a UTF-8 BOM (legal on disk, rejected by `ast.parse`) are now parsed correctly across discovery, signals, freshness, and impact analysis.
- 30 new hermetic tests — 219 total, all passing.
- Validated locally end-to-end: signed push events ran all three actions with the registry untouched (no auto-landing), bad signatures got 401, and all four KPIs computed from real demo artifacts.

### Added (Plan 5: A2A multi-agent collaboration)

- `src/agents/`: schema-first A2A protocol (`Agent` base class: `name`/`role`/`input_schema`/`output_schema` with jsonschema validation, an `AGENTS` registry and `describe_agents()` contract dump for future runtimes) plus six agents — `analysis` (wraps discovery), `evidence` (evidence-chain aggregation, confidence, owner inference), `knowledge` (v2 unit drafts + owner questions), `risk` (signal-based risk grading; hard conflicts: revert evidence, scope overlaps; soft warnings: no evidence, missing/inferred owner), `review` (confirm / ask_owner / human_review recommendations with diff summaries), `injection` (context previews that prove what knowledge reaches a developer).
- `src/agents/orchestrator.py` + `kc run`: sequential in-process orchestration (analysis → evidence → knowledge → risk → patch → review → injection) with per-message schema validation, graceful per-stage failure, `--stop-after`, PENDING patch-proposal materialization for drafts matching existing units (never auto-landed), and a full-chain `run_<ts>.json` report.
- Injection agent degrades gracefully to unmatched previews when no registry exists (also auto-discovers `<repo>/.knowledge-ci/data/registry.json`).
- 23 new hermetic tests — 242 total, all passing.
- Validated on `psf/requests` (@80683562): one `kc run` command executed all seven stages (37 modules, 24 drafts, 47 questions, 24 reviews, 5 injection previews); with a registry configured, 3 PENDING patch proposals were materialized with valid Delta ops, and every review honestly recommended `human_review` for unconfirmed owners.

### Fixed (A2A pipeline audit: data-loop hardening)

- Evidence → knowledge data loop closed: the evidence agent now outputs the full enriched candidates (recomputed confidence, `evidence_ids`, owner inference) and the knowledge agent consumes only that output — never the raw discovery report; enriched candidates are persisted in the run report.
- Strict interface contracts: shared JSON Schemas in `src/agents/schemas.py` (full per-field types for candidates, evidence items, risk/review entries, previews) with `additionalProperties: false` on every agent message; contracts now include `version` and `effects` (`analysis` declares `artifacts:reports`, all others `none`).
- Unified failure model: every stage failure carries `error_type` (`input_schema` / `output_schema` / `runtime` / `data`) plus a structured error; a crashing agent is proven by test to be isolated (stage failed, downstream skipped, pipeline completes).
- Risk semantics split: `signal_risk` (inherent structural risk) vs `review_risk` (conflicts → HIGH; ≥2 warnings → HIGH; confidence < 0.3 → HIGH; otherwise signal risk), so two drafts of the same signal kind can carry different review risk based on evidence quality; patch proposals now use `review_risk`.
- Same-unit patch proposals deduplicated: per unit only the highest `review_risk` draft becomes a PENDING proposal, the rest are recorded as deferred — preventing parallel patches from silently overwriting each other; empty summaries are skipped.
- Injection targets now equal the knowledge impact surface: registry unit scope files first, then Top-K module paths (deduplicated, capped at 10) — validated with a matched preview on the managed unit.
- Review `ask_owner` entries carry the draft's question list, and the run report exposes a `candidates` alias so `kc ask-owner` can consume `kc run` reports directly (closing the human loop).
- Documented semantics: `confirm` is a recommendation, not a decision (decisions happen in `kc apply` / `kc ask-owner --confirm`); the 0.5 confirm threshold equals one mr or two commits of evidence strength.
- 8 new/updated hermetic tests — 248 total, all passing.

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
