# Changelog

## [0.1.1] - 2026-10-13

### Fixed
- **Two Blenders could share the bridge port on Windows.** The socket used `SO_REUSEADDR`, which on Windows lets a
  second process bind the same port, so Claude's tool calls could land in the wrong Blender. The bridge now binds
  exclusively (`SO_EXCLUSIVEADDRUSE`).

### Changed
- A second Blender automatically uses the next free port (9878, 9879, …). Its in-Blender chat follows that port, and
  the Bridge panel says that Claude Code is talking to the other Blender.

## [0.1.0] - 2026-10-13

First release: in-Blender chat on your Claude subscription, token-lean MCP ops for Claude Code, saved chats, image
attachments with Ctrl+V paste, the `blender-bridge` skill, and installer and test suites.
