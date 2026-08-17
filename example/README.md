# Mini Pay 示例项目 / Mini Pay Example Project

这是一个用于演示 Knowledge CI 的最小示例项目。
This is a minimal example project used to demonstrate Knowledge CI.

## 结构 / Layout

```text
example/
├── .knowledge-ci/
│   ├── config.yaml          # 指向项目根的配置 / config pointing at the project root
│   └── data/registry.json   # 两个示例知识单元 / two sample knowledge units
├── docs/payment-spec.md     # 业务文档（知识来源）/ business docs (knowledge source)
└── src/payment/
    ├── retry.py             # 知识单元 payment_retry 绑定的文件
    └── refund.py            # 知识单元 payment_refund 绑定的文件
```

## 快速体验 / Try It

在 `example/` 目录下运行 / Run from the `example/` directory:

```powershell
# 1. 注入上下文（无需 LLM，无需 git）/ Inject context (no LLM, no git needed)
python ..\scripts\inject_context.py --file src/payment/retry.py

# 2. 让示例成为一个 git 仓库并提交，体验影响分析 / Make it a git repo and commit, then analyze
git init
git add .
git commit -m "initial example"
git log --format=%H -1   # 记下 commit hash / note the hash
python ..\scripts\analyze_commit.py --hash <commit>

# 3. 生成知识补丁（需 OPENAI_API_KEY）/ Generate a patch (needs OPENAI_API_KEY)
python ..\scripts\generate_patch.py --commit <commit> --unit payment_retry

# 4. 审核通过后落地 / Apply after review
python ..\scripts\apply_patch.py --patch .knowledge-ci\data\patches\patch_<id>.json
```

> 提示：`.knowledge-ci/data/patches` 与 `reports` 目录由工具自动创建。
> Tip: the `patches` and `reports` directories are created automatically.
