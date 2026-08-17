# 快速上手 / Quickstart

## 1. 安装 / Install

```powershell
git clone https://github.com/<your-account>/knowledge-ci.git
cd knowledge-ci
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

依赖 / Dependencies：`openai`（LLM 补丁生成）、`GitPython`（提交分析）、`pyyaml`、`jsonschema`。

## 2. 接入你的项目 / Onboard Your Project

```powershell
python scripts\init_project.py --project C:\path\to\your-project
```

生成 / Creates：

```text
your-project/
└── .knowledge-ci/
    ├── config.yaml               # project_path 指向项目根
    ├── data/
    │   ├── registry.json         # 空注册表（从这里开始录入知识单元）
    │   ├── registry.example.json # 完整字段示例，照着复制
    │   ├── patches/              # 补丁输出
    │   └── reports/              # 影响报告
```

init 会扫描项目并列出建议优先录入的文件（体积较大、非测试目录的源码文件）。

## 3. 录入知识 / Enter Knowledge

在 `.knowledge-ci/data/registry.json` 的 `units` 里为**核心/高风险模块**添加单元：

```json
{
  "id": "order_settlement",
  "name": "订单结算 / Order settlement",
  "file_pattern": "src/settlement/*.py",
  "risk_level": "HIGH",
  "knowledge_delta": { "ops": [ { "insert": "结算必须在日切后执行，且金额以财务复核结果为准。" } ] },
  "related_docs": ["docs/settlement.md"],
  "last_verified": "2026-08-17",
  "code_hash": "",
  "version": 1
}
```

规则 / Rules：`risk_level` ∈ HIGH/MEDIUM/LOW；`knowledge_delta.ops` 是 Quill Delta 数组，
直接用 `[{"insert": "文本"}]` 即可；`file_pattern` 支持 glob，多个匹配时取最长。

## 4. 配置 LLM / Configure the LLM

```powershell
# OpenAI
$env:OPENAI_API_KEY = "sk-..."

# DeepSeek（或其他 OpenAI 兼容接口）/ DeepSeek (or any compatible endpoint)
$env:OPENAI_API_KEY = "sk-..."
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
```

验证 / Verify：`python scripts\check_llm.py`
持久化 / Persist：Windows 运行 `scripts\set_api_key.ps1`；macOS/Linux 写入 `~/.bashrc` 等。

模型名在 `.knowledge-ci/config.yaml` 的 `model` 字段（默认 `deepseek-chat`，可用 `gpt-4o-mini`）。

## 5. 日常使用 / Daily Use

在**项目目录**下运行（自动发现 `.knowledge-ci/config.yaml`）。
Run from your **project directory** (config auto-discovery):

```powershell
# AI 改代码前，注入上下文 / inject before AI edits
python C:\path\to\knowledge-ci\scripts\inject_context.py --file src\settlement\worker.py

# 提交后分析影响 / analyze after a commit
python C:\path\to\knowledge-ci\scripts\analyze_commit.py --hash 36e4a824

# 为受影响单元生成补丁 / generate a patch for an affected unit
python C:\path\to\knowledge-ci\scripts\generate_patch.py --commit 36e4a824 --unit order_settlement

# 人工审核补丁后落地 / apply after review
python C:\path\to\knowledge-ci\scripts\apply_patch.py --patch .knowledge-ci\data\patches\patch_kp_<id>.json
```

审核参考 / Review aids：

- 补丁自带 Quill 预览（`preview_delta`），用
  `python C:\path\to\knowledge-ci\scripts\feedback_server.py --port 8080` 后打开
  `http://localhost:8080/?delta=<preview_delta>` 看前后对比。
- 驳回后携带意见重新生成 / Regenerate with review feedback:
  `generate_patch.py ... --review-feedback "影响范围描述不实，请修正"`

## 6. IDE 集成 / IDE Integration

- Cursor：复制 `templates/cursor-knowledge-ci.mdc` 到项目 `.cursor/rules/`，改 globs 与路径。
- VS Code：复制 `.vscode/tasks.json` 到项目，改脚本路径，用任务
  "Knowledge CI: Inject Context" 调用。

## 常见问题 / FAQ

- **没配 LLM Key 能用吗？** 影响分析和注入完全本地运行；补丁生成可用
  `--mock-response-file` 离线验证。
- **注入会花钱吗？** 不会。注入只读本地 JSON，不调用 LLM。
- **知识目录可以提交进我的项目仓库吗？** 可以，这正是推荐做法（知识随项目版本化）；
  `.knowledge-ci/data/{patches,reports,feedback.jsonl}` 建议加进 `.gitignore`。
