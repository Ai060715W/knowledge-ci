# 架构说明 / Architecture

## 模块 / Modules

```text
knowledge-ci/
├── scripts/                 CLI 入口 / entry points
│   ├── init_project.py      初始化 .knowledge-ci/ / onboarding
│   ├── analyze_commit.py    变更捕获与影响分析 / impact analysis
│   ├── generate_patch.py    LLM 补丁生成 / patch generation
│   ├── apply_patch.py       补丁落地 / patch application
│   ├── inject_context.py    上下文注入 / context injection
│   ├── feedback_server.py   预览 + 反馈服务 / preview + feedback
│   ├── check_llm.py         LLM 连通性检查 / connectivity check
│   └── set_api_key.ps1      Windows 密钥配置 / key helper
├── src/
│   ├── config.py            配置发现与路径解析 / config discovery & path resolution
│   ├── registry/matcher.py  文件 → 知识单元匹配（glob 最长匹配）
│   ├── impact/analyzer.py   提交 diff 提取、符号摘要、影响计算、文档建议
│   ├── patch/
│   │   ├── delta.py         Quill Delta 校验、应用、文本互转
│   │   ├── prompts.py       高/低危 Prompt 模板 + Few-shot
│   │   ├── generator.py     补丁构建（校验 + 自纠错重试 + 审核意见修正）
│   │   └── pr_manager.py    PR 审核辅助、补丁落地、状态流转
│   └── inject/context.py    注入上下文构建、token 预算、反馈记录
├── preview/index.html       Quill 静态预览器（补丁前后对比）
├── templates/               Cursor Rules 模板
└── tests/                   33 个单元测试 / unit tests
```

## 数据流 / Data Flow

```text
git commit
   │  GitPython diff（按 added/modified/deleted 分类，过滤 .py/.js/.ts/.java）
   ▼
analyze_commit ── match_unit(file_pattern glob, 最长匹配) ──► impact_<commit>.json
   │  affected_units + unmanaged_files + 符号摘要 + 文档建议
   ▼
generate_patch ── 高/低危 Prompt + 旧知识 + 变更摘要 ──► LLM ──► Delta ops
   │  校验：JSON 数组、retain/delete/insert 互斥且合法、模糊词禁用
   │  失败 → 把错误回喂模型重试（≤3 次）；--review-feedback → 按审核意见修正
   ▼
patch_kp_<date>_<seq>.json（PENDING）──► 人工/PR 审核（预览器对比前后文本）
   │  通过 → apply_patch.py：应用 Delta、version+1、last_verified/code_hash 更新、标记 APPLIED
   │  驳回 → REJECTED + status_reason
   ▼
registry.json（事实来源 / single source of truth）
   ▼
inject_context ── 匹配单元 → 渲染知识摘要 + 风险等级 + 历史决策（仅 APPLIED）+ 影响警告 + 最近验证
   │  500-token 预算内按优先级压缩；未匹配文件输出补充提示
   ▼
stdout → Cursor Rules / VS Code 任务 → AI 编码上下文
feedback_server：/feedback 端点 → data/feedback.jsonl（JSON Lines）
```

## 关键设计决策 / Key Design Decisions

1. **LLM 只生成候选，不直接落地 / LLM proposes, humans dispose**：所有补丁必须经过校验和审核，
   这是防"幻觉知识"入库的核心门禁。
2. **注入零 LLM 成本 / Zero-cost injection**：注入只读本地 registry 渲染文本，每天高频使用不花钱。
3. **补丁是操作而非快照 / Patches as operations**：Quill Delta 表达知识变化，可预览、可回滚、可追溯。
4. **影响分析保持轻量 / Lightweight impact analysis**：文件 glob 匹配 + 正则/AST 符号摘要，
   不做全量依赖图（列入路线图）。
5. **审核复用现有流程 / Reuse existing review flows**：GitHub PR + 本地文件状态机，不另建后台系统。

## 验证结果（源自 4 周 POC，Flask 实验项目）/ Validation Results (from the 4-week Flask POC)

| 指标 / Metric | 结果 / Result |
|:---|:---|
| 影响覆盖率 / Impact coverage | 100%（3/3 历史 Commit） |
| 补丁盲评平均分 / Blind-review avg score | 4.17/5（首轮）；审核修正闭环后落地知识 100% 通过 |
| 注入提升率 / Injection uplift（A/B，LLM 模拟编码） | 有注入 77.8% vs 无注入 0% 一次性通过（+77.8pp） |
| 性能 / Performance | analyze 0.38~0.68s；inject 0.22~0.24s |
| 成本 / Cost | 全流程真实调用 ≈ ¥0.12（deepseek-chat 计价） |

重要教训 / Lessons learned：

- 模型会自信断言输入中不存在的依赖事实 → 审核门禁 + `--review-feedback` 修正闭环有效。
- 同单元并行补丁需按 `old_version` 校验并重基 → 已列入路线图。
- Prompt 提供旧文本总字符数并引导"大改全量替换"，显著降低 Delta 偏移计算错误。

## 扩展点 / Extension Points

- 新语言支持：修改 `src/impact/analyzer.py` 的 `CODE_SUFFIXES` 与正则常量。
- 新 IDE：消费 `inject_context.py --json` 输出即可（稳定字段：unit_id、risk_level、knowledge_summary、history_decisions、impact_warnings、last_verified）。
- 新模型：任何 OpenAI 兼容接口，改 `OPENAI_BASE_URL` 与 config `model`。
