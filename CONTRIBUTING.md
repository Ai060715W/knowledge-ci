# Contributing

感谢你的兴趣！Thanks for your interest!

## 报告问题 / Reporting Issues

- 提交前先搜索已有 issue / Search existing issues before filing a new one.
- 描述复现步骤、期望行为与实际行为 / Include steps to reproduce, expected vs actual behavior.
- 不要在任何 issue 中粘贴 API Key / Never paste API keys in issues.

## 提交代码 / Pull Requests

1. Fork 并克隆 / Fork and clone the repository.
2. 新建分支 / Create a branch: `git checkout -b feat/your-change`.
3. 修改后运行测试 / Run the tests after your change:
   ```powershell
   python -m unittest discover -s tests
   ```
4. 保持提交信息清晰 / Keep commit messages clear.
5. 提交 PR 时说明动机与改动范围 / Explain the motivation and scope in the PR.

## 风格 / Style

- 遵循现有代码风格（Python 3.10+，类型注解，docstring 说明"为什么"）。
  Follow the existing style (Python 3.10+, type hints, docstrings explain the "why").
- 新增行为请补测试 / Add tests for new behavior.

## 开发约定 / Conventions

- 所有脚本入口位于 `scripts/`，库代码位于 `src/`。
  Entry points live in `scripts/`, library code in `src/`.
- 路径与配置解析统一走 `src/config.py`。
  Path/config resolution goes through `src/config.py`.
- 用户数据（registry/patches/reports/feedback）不提交进本仓库。
  User data (registry/patches/reports/feedback) is never committed here.
