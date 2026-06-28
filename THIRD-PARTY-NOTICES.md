# 第三方组件说明

中文 | [English](THIRD-PARTY-NOTICES.en.md)

AgentSight for Windows 当前核心包不强制安装截图或打包依赖。可选依赖包括：

- `mss`：跨平台屏幕捕获候选；
- `Pillow`：图像处理、测试 PNG 生成、部分 review artifact；
- `windows-capture`：Windows Graphics Capture 路径；
- `PyInstaller`：本地 exe 打包。

发布前请重新核对 `pyproject.toml`、锁定的构建环境和 release artifacts 中实际包含的第三方组件。

本项目只读参考过 LinkShelf / ServicePilot 的发布文档风格和 workflow 组织方式，没有复制它们的实现代码。
