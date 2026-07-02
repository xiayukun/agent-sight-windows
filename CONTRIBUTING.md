# 贡献指南

中文 | [English](CONTRIBUTING.en.md)

AgentSight for Windows 仍处于 research/MVP 阶段。贡献应围绕一个清晰工程里程碑推进，并保持像素级观察、人类等价输入和可复核证据链的边界。

## 本地开发

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

可选截图/输入依赖不放入基础安装。需要打包 exe 时再安装：

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## 工程规则

- 不新增 OCR、clipboard、DOM、accessibility tree、window semantics、隐藏应用 API、cmd/shell GUI 替代或业务成功判断。
- 不改变 Host Agent 输入/截图核心链路，除非当前阶段明确要求。
- raw evidence 必须和 cursor overlay、diff heatmap、annotated review artifact 分离。
- Tray GUI 只是人类可见控制面，不是 AI 语义通道。
- 不提交 `runs*`、`dist/`、`build/`、本地缓存、截图证据或 token。
- 不在当前活跃工作区中直接把 `agentsight` 目录改名；公开品牌先用文档表达。

## Pull Request 检查

- [ ] 变更保持项目边界。
- [ ] 测试覆盖新增行为和失败路径。
- [ ] README / Skill / user guide 已随 AI 或用户流程变化更新。
- [ ] 没有提交本地运行证据、构建产物或私密路径。
- [ ] 安全敏感变更说明了 token、localhost、operator control、emergency stop 和真实输入影响。
