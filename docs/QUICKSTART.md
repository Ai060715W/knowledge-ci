# 使用教程 / Tutorial

> 这篇教程的目标读者：刚 clone 本仓库、想把它用在自己项目上的人。
> 跟着走完「路径 A」（10 分钟）就能完整使用核心闭环；「路径 B」展示自动发现与持续演进。
> 本文所有命令都给出 `kc` 与 `scripts/` 两种形式（`kc` 需要 `pip install -e .`）。

## 0. 三十秒理解这个工具 / What It Is in 30 Seconds

Knowledge CI 帮你解决一个具体问题：**代码一直在变，团队知识却总是滞后**。

它做的事分两步：

1. **维护知识**：把"这个模块为什么必须这样做"（业务规则、历史事故、架构妥协）存成
   结构化知识单元，代码变化时自动检测知识是否过期、自动生成更新补丁（人工审核后落地）。
2. **使用知识**：开发者（或 AI 编码工具）修改代码前，自动注入相关知识、风险和历史决策。

```text
发现 → 证据 → 追问 → 沉淀 ──► 注入 ──► 演进（新鲜度检测 → 补丁 → 审核 → 落地）
```

需要的环境 / Prerequisites：**Python 3.10+ 与 git**（在 PATH 中即可），其余零依赖安装。

## 1. 安装 / Install

```powershell
git clone https://github.com/Ai060715W/knowledge-ci.git
cd knowledge-ci
python -m venv venv
venv\Scripts\activate                 # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
pip install -e .                      # 可选：提供统一 `kc` 命令；不装也能用 scripts\ 下的脚本
```

依赖 / Dependencies：`openai`（LLM 补丁生成）、`GitPython`（git 历史分析）、`pyyaml`、`jsonschema`。

## 2. 接入你的项目 / Onboard Your Project

在**你的项目目录**里初始化（Knowledge CI 只往里面写一个 `.knowledge-ci/` 目录）：

```powershell
kc init --project C:\path\to\your-project
```

生成 / Creates：

```text
your-project/
└── .knowledge-ci/
    ├── config.yaml               # 全部路径与模型配置（自动发现）
    └── data/
        ├── registry.json         # 知识注册表（schema v2，唯一事实来源）
        ├── registry.example.json # 完整字段示例，照着复制
        ├── patches/              # 知识补丁（PENDING 待审核 → APPLIED/REJECTED）
        ├── reports/              # 影响/发现/新鲜度报告
        ├── evidence/             # 证据文档
        └── metrics/              # 指标输出
```

之后所有命令在**项目目录**下运行即可（自动发现 `.knowledge-ci/config.yaml`）。
也可以不 cd，用 `--config C:\path\to\your-project\.knowledge-ci\config.yaml` 显式指定。

> 旧版（schema v1）项目升级：`kc migrate --registry <path>`（支持 `--dry-run` / 自动备份 / `--rollback`）；
> 不迁移也继续可用。

## 3. 路径 A：零 LLM Key 的最小闭环（先跑通再谈钱）/ Path A: the Free Core Loop

不配任何 Key 也能用完整闭环——补丁生成是唯一需要 LLM 的环节，且支持离线 mock。

### 3.1 录入第一条知识 / Enter Your First Knowledge

把 `.knowledge-ci/data/registry.json` 里的 `units` 填上**核心/高风险模块**的知识：

```json
{
  "id": "order_settlement",
  "title": "订单结算 / Order settlement",
  "summary": "结算必须在日切后执行，且金额以财务复核结果为准。",
  "rationale": "日切前的流水可能被冲正，以复核结果为准避免重复入账。",
  "scope": { "files": ["src/settlement/*.py"], "symbols": ["SettlementJob"] },
  "evidence": [{ "type": "commit", "id": "9f3c21a" }],
  "confidence": 0.7,
  "owner": "settlement-team",
  "reviewer": null,
  "status": "active",
  "risk_level": "HIGH",
  "knowledge_delta": { "ops": [ { "insert": "结算必须在日切后执行，且金额以财务复核结果为准。" } ] },
  "related_docs": ["docs/settlement.md"],
  "last_verified": "2026-08-17",
  "code_hash": "",
  "version": 1
}
```

要点 / Key rules：

- `knowledge_delta.ops` 是 Quill Delta 数组，普通文本直接 `[{"insert": "文本"}]`；
- `scope.files` 支持 glob（`src/payment/*.py`、`src/refund/**/*.py`），多个匹配取最长；
- `status` 走状态机 `proposed → under_review → active → outdated → retired`，**只有 active 参与注入**；
- 完整字段与状态机说明见 [CONFIG.md](CONFIG.md)。

### 3.2 改代码前注入 / Inject Before Edits（0 成本）

```powershell
kc inject --file src\settlement\worker.py
# 输出：模块/风险等级/知识摘要/历史决策/影响警告/最近验证（默认 500 token 内）
```

未匹配文件会提示"暂无知识记录"——这正是你该补知识的地方。

### 3.3 提交后分析影响 / Analyze After a Commit

```powershell
kc analyze --hash 36e4a824
# 输出影响报告：变更文件、受影响单元、未受管文件、关联文档建议
```

### 3.4 生成知识补丁（离线可验证）/ Generate a Patch (offline-capable)

```powershell
# 有 LLM Key（见第 4 节）时：
kc generate --commit 36e4a824 --unit order_settlement

# 没有 Key 时用 mock 离线验证整条链路：
kc generate --commit 36e4a824 --unit order_settlement --mock-response-file mock.json
```

补丁永远是 **PENDING**：通过 Delta 校验与模糊词检查后写入 `patches/`，等待人工审核。

### 3.5 审核与落地 / Review & Land

```powershell
# 看前后对比（静态 Quill 预览器）：
kc feedback --port 8080
# 打开补丁 JSON 里的 preview_delta，访问 http://localhost:8080/?delta=<preview_delta>

# 确认无误后落地（知识版本 +1，状态机流转 active）：
kc apply --patch .knowledge-ci\data\patches\patch_kp_<id>.json

# 驳回后带意见重新生成：
kc generate --commit 36e4a824 --unit order_settlement --review-feedback "影响范围描述不实，请修正"
```

## 4. 配置 LLM / Configure the LLM

```powershell
# OpenAI 或任意 OpenAI 兼容接口（DeepSeek 等）
$env:OPENAI_API_KEY = "sk-..."
$env:OPENAI_BASE_URL = "https://api.deepseek.com"   # 用 DeepSeek 等兼容接口时设置

kc check-llm        # 验证连通性
```

- 模型名在 `.knowledge-ci/config.yaml` 的 `model`（默认 `deepseek-chat`，可用 `gpt-4o-mini`）；
- 持久化：Windows 运行 `scripts\set_api_key.ps1`；macOS/Linux 写入 shell 配置。

## 5. 路径 B：自动发现 → 追问 → 落库的全闭环 / Path B: Automated Discovery Loop

不想手工录入知识？让系统从代码与 git 历史里**发现候选**，人来确认：

```powershell
# 1. 发现：Top-K 模块 + 候选知识 + 追问清单（只读、零 LLM）
kc discover --repo C:\path\to\your-project --out .knowledge-ci\data\reports

# 2. 把候选变成正式追问文件
kc ask-owner --action questions --report .knowledge-ci\data\reports\discovery_<ts>.json

# 3. 人工回答 + 确认落库（候选 → under_review，证据链追加 human_answer、重算置信度）
kc ask-owner --action answer --questions .knowledge-ci\data\reports\questions_<ts>.json `
    --report .knowledge-ci\data\reports\discovery_<ts>.json --candidate cand_xxx_001 `
    --answer "该阈值来自协议 SPEC-1，不可修改。" --owner payment-team `
    --confirm --registry .knowledge-ci\data\registry.json
```

- 每一步产物都可回溯：候选带**证据链**（真实 commit id + 角色 introduced/modified/reverted）；
- 负责人推断（CODEOWNERS 优先、`git blame` 回退）只是**建议值**，`--owner` 确认后才算数。

## 6. 保持知识新鲜 / Keep Knowledge Fresh（四层判断）

代码变了，知识还对不对？一条命令告诉你，且**每一步可解释**：

```powershell
# 只读检查（默认）：时间初筛 → AST 语义过滤 → 依赖影响 → LLM 终判
kc freshness --repo C:\path\to\your-project --registry .knowledge-ci\data\registry.json

# 应用安全簿记：仅刷新验证时间戳 + active→outdated 状态流转（不改知识文本）
kc freshness --apply

# partial_update 自动生成 PENDING 补丁（仍须人工审核）
kc freshness --auto-patch --patches .knowledge-ci\data\patches
```

报告 `freshness_<ts>.json` 里每个单元带**逐层决策日志**（命中 commit、每文件 AST 判定、依赖边）。
没配 Key 时进入 LLM 层的单元标记 `needs_llm`，不报错；`new_knowledge` 判定的草稿可直接交
`kc ask-owner`（报告带 `candidates` 字段）。

## 7. 一条命令跑全流程 / One Command: the A2A Pipeline

把上面所有环节串成一条流水线（schema 校验、失败隔离、可 `--stop-after`）：

```powershell
kc run --repo C:\path\to\your-project --top-k 10 --out .knowledge-ci\data\reports `
       --registry .knowledge-ci\data\registry.json --patches .knowledge-ci\data\patches
```

按顺序执行：analysis（发现）→ evidence（证据增强）→ knowledge（草稿+追问）→ risk（signal/review
风险分级与冲突检测）→ patch（命中现有单元的草稿物化为 PENDING 提案，同单元去重）→ review
（confirm / ask_owner / human_review 建议）→ injection（受管文件上下文预览）。

产物 `run_<ts>.json`：`enriched` / `drafts`（即 `candidates`，可直接交 `kc ask-owner`）/ `risks` /
`reviews` / `proposals` / `injection_previews`。

## 8. 事件触发与指标 / Event Triggers & Metrics（进阶）

```powershell
# Push/MR 自动触发（GitHub 签名校验；产物只写 reports/patches，绝不自动落地）
kc webhook --repo C:\path\to\your-project --repo-name owner/repo --secret <github-secret>

# 四大 KPI：覆盖率 / 新鲜度 / 命中率 / 确认率（每项带公式与口径说明）
kc metrics --repo C:\path\to\your-project
```

多仓映射与事件开关在 `config.yaml` 的 `webhook:` 段（见 [CONFIG.md](CONFIG.md)）。

## 9. IDE 集成 / IDE Integration

- **Cursor**：复制 `templates/cursor-knowledge-ci.mdc` 到项目 `.cursor/rules/`，改 globs 与路径；
- **VS Code**：复制 `.vscode/tasks.json`，运行任务 "Knowledge CI: Inject Context"。

## 常见问题 / FAQ

- **没配 LLM Key 能用吗？** 能。注入、分析、发现、追问、新鲜度前三层、指标全部本地运行；
  补丁生成可用 `--mock-response-file` 离线验证。
- **注入会花钱吗？** 不会。注入只读本地 JSON，不调用 LLM（约 0.2s）。
- **知识目录要提交进项目仓库吗？** 推荐提交（知识随代码版本化）；生成物
  `.knowledge-ci/data/{patches,reports,evidence,metrics,feedback.jsonl}` 建议加入 `.gitignore`。
- **支持哪些语言？** v1 的发现/新鲜度 AST 分析仅 Python；`.js/.ts/.java` 的注入与影响分析可用
  （正则符号摘要），未来按插件式扩展。
- **我的仓库不是 git？** 大部分功能优雅降级（报告注明），时间/证据类功能需要 git。
- **报告里有文件被跳过（parse error）？** 语法错误文件被保守跳过并记入报告；带 UTF-8 BOM 的
  合法文件已支持。

## 命令速查 / Command Reference

| 命令 / Command | 作用 / Purpose |
|:---|:---|
| `kc init --project <path>` | 初始化项目的 `.knowledge-ci/` |
| `kc migrate --registry <path>` | v1 注册表升级 v2（dry-run/备份/回滚） |
| `kc inject --file <path>` | AI 编码前注入知识上下文 |
| `kc analyze --hash <c>` | 提交影响分析 |
| `kc discover --repo <path>` | 发现 Top-K 模块与候选知识（只读） |
| `kc ask-owner --action questions\|answer` | 追问生成与人工回填（--confirm 落库） |
| `kc freshness [--apply] [--auto-patch]` | 四层新鲜度判断 |
| `kc generate --commit <c> --unit <id>` | LLM 生成知识补丁（PENDING） |
| `kc apply --patch <file>` | 审核通过后落地补丁 |
| `kc feedback --port 8080` | 补丁预览器 + 反馈收集 |
| `kc run --repo <path>` | A2A 流水线一条命令跑全流程 |
| `kc webhook --secret <s>` | Push/MR 事件触发服务器 |
| `kc metrics` | 四大 KPI 指标 |
| `kc check-llm` | LLM 连通性检查 |

## 进一步阅读 / Further Reading

- [CONFIG.md](CONFIG.md) —— 全部配置项、schema v2 字段、状态机、四层口径、KPI 公式；
- [ARCHITECTURE.md](ARCHITECTURE.md) —— 数据流、设计决策、POC 验证数据；
- `example/` —— 一个带知识单元的最小演示项目，可直接运行体验。
