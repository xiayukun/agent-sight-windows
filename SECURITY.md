# 安全策略

中文 | [English](SECURITY.en.md)

AgentSight for Windows 是本地操作者授权的 Windows GUI 控制工具，不是沙箱，也不是安全边界。它可以在允许时移动真实鼠标、发送键盘输入并保存真实屏幕证据，因此任何常驻、token、operator control、emergency stop 相关变更都要按安全敏感变更处理。

## 报告漏洞

如果仓库启用了 GitHub private vulnerability reporting，请优先使用。否则请先开一个不含利用细节的最小公开 issue，说明有安全报告可提供。

报告中请包含：

- Windows 版本和登录会话状态；
- Host Agent / Supervisor / Tray GUI 是否运行；
- operator control 是否允许；
- emergency stop 是否启用；
- 涉及的本地 endpoint、MCP tool 或命令；
- 已脱敏的证据路径、receipt、replay 或日志。

## 明确边界

AgentSight 不提供 OCR、clipboard、DOM、accessibility tree、window semantics、隐藏应用 API、cmd/shell GUI 替代、目标命中判断、因果判断或业务成功判断。

## 高风险区域

- Host Agent bearer token 和 discovery 文件权限；
- localhost 绑定和 Host / Origin 检查；
- stale discovery 与旧进程残留；
- 真实鼠标键盘输入授权；
- operator pause / allow 与 emergency stop；
- 锁屏、UAC、安全桌面检测；
- 证据目录中的截图泄露。
