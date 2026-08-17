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
| `feedback_path` | string | 反馈 JSONL 日志 / feedback JSONL log |
| `model` | string | 补丁生成所用模型 / patch-generation model |

## LLM 环境变量 / LLM Environment Variables

| 变量 / Variable | 说明 / Meaning |
|:---|:---|
| `OPENAI_API_KEY` | 必需（补丁生成时）/ required for patch generation |
| `OPENAI_BASE_URL` | 可选：OpenAI 兼容接口地址，如 `https://api.deepseek.com` / optional compatible endpoint |
| `KNOWLEDGE_CI_MODEL` | 可选：覆盖 config 中的模型名（check_llm.py 使用）/ optional model override |

## 知识单元 Schema / Knowledge Unit Schema

`registry.json` 顶层 / Top level:

```json
{
  "version": 1,
  "last_updated": "2026-08-17",
  "units": []
}
```

单元字段 / Unit fields:

| 字段 / Field | 类型 / Type | 说明 / Meaning |
|:---|:---|:---|
| `id` | string | 唯一标识，稳定可读（如 `payment_retry`）/ stable readable id |
| `name` | string | 展示名（注入输出与 PR 描述使用）/ display name |
| `file_pattern` | string | glob 规则匹配代码文件（支持 `*`、`**`）/ code file glob |
| `risk_level` | enum | `HIGH` / `MEDIUM` / `LOW`，决定 Prompt 与注入警告强度 |
| `knowledge_delta` | object | Quill Delta 格式知识内容，必须含 `ops` 数组 / Delta ops array |
| `related_docs` | array | 关联文档路径（知识来源追溯）/ related doc paths |
| `last_verified` | string | 最近验证日期 ISO 格式 / last verified date |
| `code_hash` | string | 最近验证时的 Git Hash / verified git hash |
| `version` | int | 知识版本号，每次落地 +1 / increments on each landed patch |

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
