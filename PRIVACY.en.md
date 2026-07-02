# Privacy

[中文](PRIVACY.md) | English

AgentSight for Windows runs locally. It does not automatically upload screenshots, input records, tokens, or evidence to a remote service.

## Local Data

Default local data directory:

```text
%LOCALAPPDATA%\AgentSight
```

It may contain:

- Host Agent discovery and token;
- Supervisor / service / tray state;
- operator-control and emergency-stop markers;
- screenshots, GIFs, review artifacts, receipts, replay, integrity evidence;
- `tray-settings.json` language preference.

## Caution

Evidence folders may contain real screen content, chat windows, notifications, paths, and account information. Redact before publishing, committing, syncing, or sharing with external reviewers.

The visual memory / attention index records pixels, time, regions, hashes, paths, and evidence references. It does not perform OCR or business semantic extraction.
