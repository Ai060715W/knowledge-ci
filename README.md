# Knowledge CI

**让代码变更自动更新团队知识，并在 AI 编码前注入最新记忆。**
**Keep team knowledge in sync with code changes, and inject that memory before AI edits.**

代码一直在变，文档和团队知识却总是滞后。Knowledge CI 把"知识维护"变成一条工程流水线：
代码提交 → 自动识别影响的知识单元 → LLM 生成知识补丁 → 人工审核落地 → AI 编码前自动注入。

Code changes constantly, but docs and team knowledge lag behind. Knowledge CI turns knowledge
maintenance into an engineering pipeline: commit → impact analysis → LLM-generated knowledge
patch → human review → automatic injection before AI edits.

```text
git commit
    │
    ▼
analyze_commit.py ──► 影响报告 impact report (affected units, unmanaged files)
    │
    ▼
generate_patch.py ──► Quill Delta 知识补丁 knowledge patch (LLM + validation + self-correction)
    │
    ▼
人工审核 review (GitHub PR / manual) ──► apply_patch.py 落地 land into registry.json
    │
    ▼
inject_context.py ──► AI 编码前注入知识摘要、风险等级、历史决策、影响警告
                      inject summary / risk / history / warnings before AI edits
```

## 特性 / Features

- **零安装 / Zero install**：纯 Python 3.10+，克隆即用，`init_project.py` 一行接入任何项目；
  也可 `pip install -e .` 获得统一 `kc` CLI（`kc init`、`kc inject`……）。
  Clone and run — `init_project.py` onboards any project in one command, or
  `pip install -e .` for the unified `kc` CLI.
- **知识即代码 / Knowledge as code**：registry.json（schema v2）+ Quill Delta 补丁，知识单元带证据链、
  置信度、负责人与状态机（proposed → under_review → active → outdated → retired），
  演化可预览、可审核、可回滚。Knowledge evolves through reviewable, revertible Delta patches.
- **LLM 只生成候选 / LLM proposes, humans dispose**：补丁必须通过 Delta 校验、模糊词检查和人工审核才能落地；
  校验失败自动回喂错误让模型自纠错（≤3 次），审核驳回可用 `--review-feedback` 修正重生成。
  Patches must pass Delta validation, fuzzy-word checks, and human review. Failed validation
  feeds back for up to 3 self-correction attempts; rejected patches can be regenerated with
  `--review-feedback`.
- **注入零成本 / Zero-cost injection**：注入只读本地 JSON 渲染文本（默认 500 tokens 内），不调用 LLM。
  Injection reads local JSON only — no LLM call, ~0.2s, under a 500-token budget.
- **轻量 / Lightweight**：影响分析基于文件 glob + 符号匹配；预览器是静态 HTML；审核复用 GitHub PR。
  Impact analysis uses file glob + symbol matching; the previewer is static HTML; review reuses GitHub PRs.

## 快速开始 / Quick Start

```powershell
# 1. 克隆仓库 / Clone the repository
git clone https://github.com/Ai060715W/knowledge-ci.git
cd knowledge-ci

# 2. 安装依赖（建议使用虚拟环境）/ Install dependencies (a venv is recommended)
python -m venv venv
venv\Scripts\activate        # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
# 可选 / Optional: 安装统一 CLI，之后可用 `kc init` 等命令替代 `python scripts\...`
pip install -e .

# 3. 接入你的项目 / Onboard your project
python scripts\init_project.py --project C:\path\to\your-project

# 4. 把核心/高风险模块录入 .knowledge-ci/data/registry.json
#    (schema v2 示例见下方"知识单元"一节 / see the knowledge-unit example below)
#    Add your core/high-risk modules to the registry.
#    已有 v1 注册表？升级：python scripts\migrate_registry.py --registry <path>（或 kc migrate）
#    Upgrading an existing v1 registry: python scripts\migrate_registry.py --registry <path> (or kc migrate)

# 5. 配置 LLM（生成补丁用）/ Configure the LLM (for patch generation)
$env:OPENAI_API_KEY = "sk-..."                       # OpenAI
$env:OPENAI_BASE_URL = "https://api.deepseek.com"    # DeepSeek 兼容接口时设置 / set for DeepSeek
python scripts\check_llm.py

# 6. 在项目目录里使用 / Use inside your project directory
cd C:\path\to\your-project
python C:\path\to\knowledge-ci\scripts\inject_context.py --file src\core\module.py
```

没有 LLM Key 也能先用：`inject_context.py` 与 `analyze_commit.py` 完全本地运行；
`generate_patch.py` 支持 `--mock-response-file` 做离线验证。
No LLM key needed for injection or impact analysis; patch generation also supports
`--mock-response-file` for offline validation.

## 命令 / Commands

在项目目录下运行（自动发现 `.knowledge-ci/config.yaml`）。
Run from your project directory (config is auto-discovered).

| 命令 / Command | 作用 / Purpose |
|:---|:---|
| `python <kc>/scripts/init_project.py --project <path>`（或 `kc init`）/ or `kc init` | 初始化项目知识目录 / initialize `.knowledge-ci/` |
| `python <kc>/scripts/migrate_registry.py --registry <path>`（或 `kc migrate`）/ or `kc migrate` | v1 注册表升级到 schema v2（dry-run/备份/回滚）/ migrate a registry to schema v2 |
| `python <kc>/scripts/analyze_commit.py --hash <commit>`（或 `kc analyze`）/ or `kc analyze` | 分析提交影响，产出报告 / impact report |
| `python <kc>/scripts/discover.py --repo <path>`（或 `kc discover`）/ or `kc discover` | 隐藏知识发现：Top-K 模块 + 候选知识 + 追问清单（只读，不调 LLM）/ hidden knowledge discovery (read-only, no LLM) |
| `python <kc>/scripts/generate_patch.py --commit <c> --unit <id>`（或 `kc generate`）/ or `kc generate` | LLM 生成知识补丁 / generate a patch |
| `python <kc>/scripts/apply_patch.py --patch <file>`（或 `kc apply`）/ or `kc apply` | 审核通过后落地补丁 / apply an approved patch |
| `python <kc>/scripts/inject_context.py --file <path>`（或 `kc inject`）/ or `kc inject` | AI 编码前注入上下文 / inject context |
| `python <kc>/scripts/feedback_server.py --port 8080`（或 `kc feedback`）/ or `kc feedback` | 补丁预览 + 反馈收集 / preview + feedback |

常用参数 / Useful flags：`inject_context.py --json --max-tokens 300 --verbose`；
`generate_patch.py --review-feedback "..."`（按审核意见修正 / regenerate from review feedback）。

## IDE 集成 / IDE Integration

- **Cursor**：把 `templates/cursor-knowledge-ci.mdc` 复制到项目的 `.cursor/rules/`，修改 globs 与命令路径。
  Copy the template into `.cursor/rules/`, adjust globs and paths.
- **VS Code**：把 `.vscode/tasks.json` 复制到项目（或全局 tasks），修改脚本路径后用
  `Ctrl+Shift+P → Tasks: Run Task → Knowledge CI: Inject Context` 调用。
  Copy `.vscode/tasks.json` into your project, adjust the script path, then run the task.

## 配置 / Configuration

`.knowledge-ci/config.yaml`（由 init 生成 / generated by init）：

```yaml
project_path: ".."              # 项目根目录（相对本文件）/ project root
registry_path: "data/registry.json"
reports_path: "data/reports"
patches_path: "data/patches"
evidence_path: "data/evidence"  # v2 证据文档 / evidence documents
metrics_path: "data/metrics"    # v2 指标输出 / metrics output
feedback_path: "data/feedback.jsonl"
model: "deepseek-chat"          # 或 gpt-4o-mini 等 OpenAI 兼容模型 / or any compatible model
# discovery / freshness / owners 功能段均有默认值，可选配置
# discovery / freshness / owners sections are optional and have defaults
```

详见 / See also: [docs/CONFIG.md](docs/CONFIG.md)

## 知识单元 / Knowledge Unit

```json
{
  "id": "payment_retry",
  "title": "支付重试 / Payment retry",
  "summary": "支付重试上限为 3 次，超时后触发补偿流程。",
  "rationale": "网关幂等性仅在 3 次内保证，超过 3 次有重复扣款风险。",
  "scope": { "files": ["src/payment/retry.py"], "symbols": ["PaymentRetry", "MAX_RETRY"] },
  "evidence": [{ "type": "commit", "id": "a13f9c2" }],
  "confidence": 0.6,
  "owner": "payment-team",
  "reviewer": null,
  "status": "active",
  "risk_level": "HIGH",
  "knowledge_delta": { "ops": [ { "insert": "支付重试上限为 3 次，超时后触发补偿流程。" } ] },
  "related_docs": ["docs/payment-spec.md"],
  "last_verified": "2026-08-17",
  "code_hash": "",
  "version": 1
}
```

`scope.files` 支持 glob（`src/payment/*.py`、`src/refund/**/*.py`），多个匹配时取最长匹配；
`scope.symbols` 提供符号级回退匹配。状态机：`proposed → under_review → active → outdated → retired`，
仅 `active` 参与注入。完整 schema 见 [docs/CONFIG.md](docs/CONFIG.md)。

## 原理 / How It Works

1. **匹配**：`analyze_commit.py` 用 GitPython 提取 diff（函数/类/常量摘要），按 `scope.files`
   （v1 为 `file_pattern`）把变更文件映射到知识单元，未匹配文件记为 unmanaged。
2. **生成**：按风险等级选择 Prompt（Few-shot 教 Quill Delta 操作），调用 LLM 生成补丁；
   输出必须通过 Delta 合法性校验与模糊词检查，失败自动重试。
3. **审核**：补丁写入 `patches/`，附预览链接（Quill 静态预览器前后对比）进入人工/PR 审核；
   PENDING 补丁不会进入注入内容。
4. **落地**：`apply_patch.py` 应用 Delta、版本 +1、更新 last_verified/code_hash，标记 APPLIED。
5. **注入**：`inject_context.py` 输出知识摘要、风险等级、历史决策（仅 APPLIED 补丁）、
   影响警告、最近验证，控制在 500 tokens 内，并附反馈链接（JSONL 记录）。

详见 / See also: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/QUICKSTART.md](docs/QUICKSTART.md)

## 示例 / Example

`example/` 是一个最小支付项目（含 2 个知识单元与业务文档），可直接体验注入与闭环，
见 [example/README.md](example/README.md)。本项目源自一个 4 周 POC（Flask 实验），
POC 的完整指标与复盘可参考 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 中的"验证结果"一节。

## 路线图 / Roadmap

- [ ] `old_version` 落地前校验 + 自动重基（同单元并行补丁冲突防护）
- [ ] 符号级/依赖图影响分析（替代纯文件路径匹配）
- [ ] 历史 Bug 教训注入（知识单元附带 lessons 字段，注入时提醒往期重要 bug）
- [ ] GitHub PR 审核流程自动化完善（CODEOWNERS 映射）
- [ ] 反馈数据驱动的摘要长度与提示优化

## 许可证 / License

[MIT](LICENSE) © Knowledge CI contributors
