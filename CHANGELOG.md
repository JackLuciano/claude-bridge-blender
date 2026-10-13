# Changelog

## [0.1.2] - 2026-10-13

### Fixed
- **Ops returning text values failed.** A `py`/`get` result such as `('Claude', True)` or `'RENDERED'` was treated as
  a vector and raised `could not convert string to float`. Text values are now returned as-is.
- **Claude couldn't see lighting changes.** `look` used the studio preview light in material mode, so lights it
  added or changed were invisible to it. Captures now use the scene's own lights and world.
- **The chat didn't scroll.** Long chats ran past the bottom of the panel and the newest reply was hidden. The
  panel now always shows the latest lines, with a note about earlier ones.
- **Markdown showed as raw symbols in the chat** (`**bold**`, `` `code` ``, `- ` bullets). It is now rendered as plain
  text with • bullets.
- **Ctrl+V could stutter Blender.** Reading the clipboard image (PowerShell, 1–3 s) ran on Blender's main thread; it
  now runs in the background.

### Added
- **`tex` op: procedural textures in one call.** Kinds are wood, fabric, marble, noise, brick, checker, tiles,
  leather and concrete. It wires in colour, roughness variation and bump, so Claude can make convincing materials
  without writing shader-node Python. Wood grain runs along the object's longest side.
- The skill now asks for bevels, subdivided soft furniture, smooth round parts and textured surfaces.

### Changed
- `look` captures hide viewport overlays (grid, outlines, gizmos) and render the camera view with the scene's
  lighting.
- Friendlier extension tagline, and the Blender Extensions listing kit in `docs/extensions-listing/`.

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
