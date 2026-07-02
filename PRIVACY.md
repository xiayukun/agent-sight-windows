# 隐私说明

中文 | [English](PRIVACY.en.md)

AgentSight for Windows 只在本机运行。它不会把截图、输入记录、token 或证据自动上传到远端服务。

## 本地数据

默认本地数据目录：

```text
%LOCALAPPDATA%\AgentSight
```

可能包含：

- Host Agent discovery 与 token；
- Supervisor / service / tray 状态；
- operator control 和 emergency stop 标记；
- 截图、GIF、review artifact、receipt、replay、integrity 等证据；
- `tray-settings.json` 语言设置。

## 注意事项

证据目录可能包含真实屏幕内容、聊天窗口、通知、路径和账号信息。发布、提交、同步或发给外部审核前必须人工脱敏。

Visual memory / attention index 只记录像素、时间、区域、哈希、路径和证据引用，不做 OCR 或业务语义提取。
