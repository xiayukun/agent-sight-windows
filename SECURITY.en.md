# Security Policy

[中文](SECURITY.md) | English

AgentSight for Windows is a local, operator-authorized Windows GUI-control tool. It is not a sandbox or a security boundary. When allowed, it can move the real mouse, send keyboard input, and retain real screen evidence. Treat changes around residency, tokens, operator control, and emergency stop as security-sensitive.

## Reporting A Vulnerability

Use GitHub private vulnerability reporting if enabled. Otherwise, open a minimal public issue that says a security report is available without including exploit details.

Please include:

- Windows version and session state;
- whether Host Agent / Supervisor / Tray GUI was running;
- whether operator control was allowed;
- whether emergency stop was active;
- local endpoint, MCP tool, or command path involved;
- redacted evidence paths, receipts, replay, or logs.

## Explicit Boundary

AgentSight does not provide OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd GUI substitutes, target-hit judgment, causality judgment, or business-success judgment.

## High-Risk Areas

- Host Agent bearer token and discovery-file permissions;
- localhost binding and Host / Origin checks;
- stale discovery and old process residue;
- real mouse/keyboard input authorization;
- operator pause / allow and emergency stop;
- lock screen, UAC, and secure desktop detection;
- screenshot leakage from evidence directories.
