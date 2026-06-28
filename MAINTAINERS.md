# 维护者

中文 | [English](MAINTAINERS.en.md)

## 当前维护策略

- 项目仍处于 research/MVP 阶段。
- 公开发布前，仓库名、release assets、证据清理、license、截图素材都需要操作者确认。
- 工程阶段应先测试，再做独立审核结论；默认不再把 subagent 审核落成本地 `docs/reviews` 文件。

## 发布前负责人检查

- README / README.en.md 首屏一致；
- release notes 中英文已更新；
- `dist/` 制品和 SHA256 checksums 来自 GitHub Actions；
- evidence / runs / 本地缓存未进入发布源；
- Run Key / Startup 指向当前 packaged Supervisor。
