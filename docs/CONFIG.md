# 配置参考 / Configuration Reference

配置文件位置：`<project>/.knowledge-ci/config.yaml`
Config file location: `<project>/.knowledge-ci/config.yaml`

所有相对路径以配置文件所在目录（`.knowledge-ci/`）为基准。
All relative paths are anchored to the config file's directory (`.knowledge-ci/`).

## 字段 / Fields

| 字段 / Field | 类型 / Type | 说明 / Meaning |
|:---|:---|:---|
| `project_path` | string | 项目根目录（相对本文件，通常为 `..`）/ project root, usually `..` |
| `registry_path` | string | 知识注册表 / knowledge registry JSON |
| `reports_path` | string | 影响报告输出目录 / impact report output dir |
| `patches_path` | string | 知识补丁输出目录 / knowledge patch output dir |
| `evidence_path` | string | 证据文档目录（v2）/ evidence document dir (v2) |
| `metrics_path` | string | 指标输出目录（v2）/ metrics output dir (v2) |
| `feedback_path` | string | 反馈 JSONL 日志 / feedback JSONL log |
| `model` | string | 补丁生成所用模型 / patch-generation model |

`evidence_path` 与 `metrics_path` 未配置时默认取 `data/evidence`、`data/metrics`。
When unset, `evidence_path` and `metrics_path` default to `data/evidence` and `data/metrics`.

## 功能段 / Feature Sections（可选，均有默认值 / optional, defaults shown）

```yaml
discovery:              # 隐藏知识发现 / hidden knowledge discovery
  enabled: true
  languages: [python]   # v1 仅解析 Python / v1 parses Python only
  top_k: 10
  long_span_lines: 80   # 超长函数/类阈值 / long function/class threshold
  exclude_paths: []     # 从发现/评分中排除的路径（前缀或 glob），如 ["tests"] / paths to exclude (prefix or glob)
  weights:              # ModuleScore 公式权重 / scoring formula weights
    change_frequency: 1.0
    dependency_centrality: 1.0
    incident_history: 1.0
    rollback_count: 1.0
    contributor_entropy: 1.0
    cross_layer_impact: 1.0

freshness:              # 新鲜度四层判断 / 4-layer freshness pipeline
  time_filter_days: 30
  ast_semantic_filter: true
  dependency_impact: true
  llm_final_judge: true

owners:                 # 负责人推断 / owner inference
  codeowners_path: ""   # 留空自动探测 CODEOWNERS/.github/CODEOWNERS / empty = auto-detect
  infer_from_git_blame: true
```

程序内读取：`src/config.py` 的 `load_settings(config_path)` 会把这些段与默认值合并，
未写入配置文件的段使用上表默认值。
In code: `load_settings(config_path)` in `src/config.py` merges these sections with
the defaults above, so omitted sections still resolve to the documented values.

## LLM 环境变量 / LLM Environment Variables

| 变量 / Variable | 说明 / Meaning |
|:---|:---|
| `OPENAI_API_KEY` | 必需（补丁生成时）/ required for patch generation |
| `OPENAI_BASE_URL` | 可选：OpenAI 兼容接口地址，如 `https://api.deepseek.com` / optional compatible endpoint |
| `KNOWLEDGE_CI_MODEL` | 可选：覆盖 config 中的模型名（check_llm.py 使用）/ optional model override |

## 知识单元 Schema v2 / Knowledge Unit Schema v2

`registry.json` 顶层 / Top level:

```json
{
  "version": 2,
  "last_updated": "2026-08-17",
  "units": []
}
```

单元字段 / Unit fields（必填 / required：`id`、`title`、`status`、`version`）:

| 字段 / Field | 类型 / Type | 说明 / Meaning |
|:---|:---|:---|
| `id` | string | 唯一标识，稳定可读（如 `payment_retry`）/ stable readable id |
| `title` | string | 知识单元标题 / knowledge title |
| `summary` | string | 可快速注入的简明结论 / injectable one-line conclusion |
| `rationale` | string | 为什么这样设计 / why it is designed this way |
| `scope` | object | `{ "files": [glob...], "symbols": [类/函数名...] }`，文件级 + 符号级定位 / file + symbol scope |
| `evidence` | array | 证据链：`{"type": "code/commit/mr/issue/incident/human_answer", "id": "...", ...}` |
| `confidence` | number \| null | 可信度 0~1；未知时 `null` / confidence 0..1, `null` when unknown |
| `owner` | string \| null | 负责人 / owner |
| `reviewer` | string \| null | 审核人 / reviewer |
| `owner_inferred` | bool | owner 是否为工具推断的建议值 / whether owner was inferred by tooling |
| `status` | enum | `proposed` → `under_review` → `active` → `outdated` → `retired`（见下方状态机） |
| `risk_level` | enum | `HIGH` / `MEDIUM` / `LOW`，决定 Prompt 与注入警告强度 |
| `knowledge_delta` | object | Quill Delta 格式知识内容，必须含 `ops` 数组 / Delta ops array |
| `related_docs` | array | 关联文档路径（知识来源追溯）/ related doc paths |
| `last_verified` | string \| null | 最近验证日期 ISO 格式 / last verified date |
| `code_hash` | string \| null | 最近验证时的 Git Hash / verified git hash |
| `version` | int | 知识版本号，每次落地 +1 / increments on each landed patch |

### 状态机 / Status State Machine

```text
proposed ──► under_review ──► active ──► outdated ──► active
    │             │              │           │
    ▼             ▼              ▼           ▼
 retired      retired        retired      retired   （终态 / terminal）
```

- 仅 `active`（或无 status 的 v1 旧数据）参与注入 / only `active` (or legacy v1 units) is injected.
- 非法流转由 `src/registry/schema.py` 的 `transition_status` 拒绝 / illegal transitions raise.
- 补丁落地（apply_patch）会把单元流转为 `active`。

### v1 兼容 / v1 Compatibility

旧注册表（`version: 1`，字段 `name` / `file_pattern`）**无需迁移即可继续使用**：
读取时 `file_pattern` 回退为 `scope.files`，`name` 回退为 `title`，无 `status` 视为 `active`。
升级到 v2：`python scripts/migrate_registry.py --registry <path>`（或 `kc migrate`），
支持 `--dry-run`、自动备份（`.v1.bak`）与 `--rollback` 回滚。
Legacy registries keep working without migration: `file_pattern` falls back to
`scope.files`, `name` falls back to `title`, missing status means `active`.
Migrate with `kc migrate --registry <path>` (dry-run, backup, and rollback supported).

## 影响报告 Schema / Impact Report Schema

```json
{
  "commit": "abc1234",
  "commit_short": "abc1234",
  "generated_at": "2026-08-17T00:00:00Z",
  "changed_files": [
    {
      "path": "src/payment/retry.py",
      "status": "modified",
      "unit_id": "payment_retry",
      "summary": { "functions": [], "classes": [], "constants": ["MAX_RETRY"], "diff_excerpt": [] }
    }
  ],
  "affected_units": ["payment_retry"],
  "unmanaged_files": ["src/utils/helper.py"],
  "related_docs_suggestions": [{ "symbol": "MAX_RETRY", "docs": ["docs/payment.md"] }]
}
```

## 补丁 Schema / Patch Schema

```json
{
  "patch_id": "kp_20260817_001",
  "status": "PENDING",
  "unit_id": "payment_retry",
  "commit": "abc1234...",
  "old_version": 1,
  "new_version": 2,
  "risk_level": "HIGH",
  "delta_ops": [{ "delete": 24 }, { "insert": "新知识文本" }],
  "reasoning": "基于 commit abc1234 的代码变更摘要更新 payment_retry。",
  "affected_files": ["src/payment/retry.py"],
  "related_docs": [],
  "preview_delta": "base64...",
  "generated_at": "2026-08-17T00:00:00Z",
  "model": "deepseek-chat",
  "source_report": "...",
  "prompt": "..."
}
```

状态 / Status：`PENDING`（审核中，不进入注入）→ `APPLIED`（已落地）或 `REJECTED`（驳回，附 `status_reason`）。
