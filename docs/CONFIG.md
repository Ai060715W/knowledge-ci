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
  confidence_weights:   # 置信度公式的按证据类型权重 / confidence weights by evidence type
    code: 0.2
    commit: 0.3
    mr: 0.5
    issue: 0.4
    incident: 0.6
    human_answer: 0.9
  weights:              # ModuleScore 公式权重 / scoring formula weights
    change_frequency: 1.0
    dependency_centrality: 1.0
    incident_history: 1.0
    rollback_count: 1.0
    contributor_entropy: 1.0
    cross_layer_impact: 1.0

freshness:              # 新鲜度四层判断 / 4-layer freshness pipeline
  time_filter_days: 30  # 第 1 层无锚点时的回退时间窗（天）/ fallback window when no anchor exists
  ast_semantic_filter: true
  dependency_impact: true
  llm_final_judge: true
  indirect_depth: 2     # 第 3 层间接命中的依赖边距离 / indirect-hit dependency edge depth
  llm_max_units: 20     # 第 4 层单次运行的最大 LLM 单元数 / max units sent to the LLM per run

owners:                 # 负责人推断 / owner inference
  codeowners_path: ""   # 留空自动探测 CODEOWNERS/.github/CODEOWNERS / empty = auto-detect
  infer_from_git_blame: true
```

程序内读取：`src/config.py` 的 `load_settings(config_path)` 会把这些段与默认值合并，
未写入配置文件的段使用上表默认值。
In code: `load_settings(config_path)` in `src/config.py` merges these sections with
the defaults above, so omitted sections still resolve to the documented values.

## 证据链与追问 / Evidence Chain & Owner Questions

`kc discover` 的候选自带证据链（commit 级、可回溯 hash）与置信度；
`kc ask-owner` 完成"证据不足 → 追问 → 人工回答 → 候选落地"闭环：

```powershell
# 1. 发现候选 / discover candidates
kc discover --repo C:\path\to\repo --out reports

# 2. 生成追问文件 / generate the questions file
kc ask-owner --action questions --report reports\discovery_<ts>.json

# 3. 人工回答并确认落地 / answer and land the candidate
kc ask-owner --action answer --questions reports\questions_<ts>.json `
    --report reports\discovery_<ts>.json --candidate cand_xxx_001 `
    --answer "该数值来自协议 SPEC-1，不能修改。" --owner payment-team `
    --confirm --registry .knowledge-ci\data\registry.json
```

- 置信度公式 / confidence formula：`confidence = 1 - Π(1 - weight[type])`，对去重后的证据类型求积；
  默认权重见上方 `confidence_weights`；无证据时为 `null`（未知，不等于 0）。
- 负责人推断 / owner inference：CODEOWNERS 优先，缺失时用 `git blame` 行数最多的作者；
  推断值一律带 `owner_inferred: true`，`--owner` 人工确认后变为 `false`。
- `--confirm` 把候选写入注册表为 `status: under_review`，证据链追加 `human_answer` 并重算置信度，
  之后复用现有补丁审核管线（generate → review → apply）走向 `active`。
- 追问不依赖任何平台通知：v1 是本地 JSON 文件 + 人工回填。

## 知识新鲜度 / Knowledge Freshness

`kc freshness` 按四层漏斗判断每条 active 知识是否仍与代码一致，
每层输出结构化决策日志（`layer`、`reason`、命中 commit/文件/依赖边），全程可解释：

| 层 / Layer | 判定 / Decision | 成本 / Cost |
|:---|:---|:---|
| 1. 时间初筛 / time | 锚点（`code_hash` → `last_verified` → `time_filter_days` 天）之后无提交 → 仍新鲜；`scope.files` 全部缺失 → 直接 outdated | 零 |
| 2. AST 语义过滤 / ast | 归一化 AST 对比：注释、格式化、docstring、import 排序、无引用局部重命名不算语义变更；解析失败保守视为语义变更 | 零 |
| 3. 依赖影响 / impact | 变更文件直接命中 `scope.files`（声明 `symbols` 时须相交），或命中距离 ≤ `indirect_depth` 的上下游模块（依赖图可达性） | 零 |
| 4. LLM 终判 / llm | `still_valid` / `partial_update`（附 Delta 补丁）/ `outdated` / `new_knowledge`（附候选草稿）；JSON schema 校验失败自动重试 ≤3 次 | 每次一次调用，单次运行上限 `llm_max_units` |

```powershell
# 只读检查（默认）/ read-only check (default)
kc freshness --repo C:\path\to\repo --registry .knowledge-ci\data\registry.json

# 应用安全簿记 / apply safe bookkeeping (timestamps + status transitions only)
kc freshness --apply

# 前三层即可 / stop before the LLM
kc freshness --no-llm

# partial_update 自动生成 PENDING 补丁（仍需人工审核落地）
kc freshness --auto-patch --patches .knowledge-ci\data\patches

# 离线验证第 4 层 / offline layer-4 verification
kc freshness --mock-response-file verdict.json
```

- **只读默认**：freshness 永不修改知识文本；`--apply` 仅刷新 `last_verified`/`code_hash`
  与状态机流转（`active → outdated`），`partial_update` 补丁永远是 `PENDING`。
- 未配置 LLM Key 时，进入第 4 层的单元标记 `needs_llm`，不报错退出。
- `new_knowledge` 候选草稿在报告里以 `candidates` 暴露，可直接交给 `kc ask-owner` 走追问闭环。
- 报告落盘：`reports/freshness_<ts>.json`。

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
