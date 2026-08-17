# Changelog

All notable changes to this project will be documented in this file.

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
