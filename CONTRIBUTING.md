# Contributing to Kairo

## 开始前

先阅读 [开发者指南](docs/developer-guide.md) 和 [系统架构](docs/architecture.md)。较大的行为变更应先说明用户场景、兼容影响和测试方案。

## 提交要求

- 改动范围聚焦，不混入无关格式化或重构。
- 新行为必须有测试，UI 行为优先使用 Textual `run_test()`。
- 配置字段、命令和模块边界变化必须同步更新文档。
- 不提交 API Key、token、私有 URL、本地路径、缓存或测试生成物。
- 保持 `--plain` 兼容路径，除非变更明确废弃它。

## Pull Request 检查表

- [ ] `python -m pytest` 通过
- [ ] `kairo --help` 通过
- [ ] 相关 Textual 和 plain 路径已验证
- [ ] 没有凭据和生成物
- [ ] 文档与 `CHANGELOG.md` 已按需更新
- [ ] 说明了剩余风险和未覆盖场景

项目采用 MIT 许可证。提交即表示你同意在 MIT 许可证下授权你的贡献。
