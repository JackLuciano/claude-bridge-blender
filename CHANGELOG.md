# Changelog

## [0.2.0] - 2026-10-14

### Security
- **Web pages could run code in Blender.** The bridge answered a non-JSON line with an error and kept reading, so a
  page could POST text to `127.0.0.1:9877` and its body ran as a batch. Requests now need a per-launch token (read
  from a temp file by the MCP server), HTTP-looking or malformed input closes the connection, and jobs that outlive
  their client are dropped instead of running late.
- **Optional safe mode**: Python, clearing the scene, opening files, deleting objects Claude didn't make and file
  access outside the project/temp folders wait for Approve/Deny in the panel.
- Coach and Plan chat modes are read-only, enforced by the bridge.

### Fixed
- The chat's CLI no longer inherits `ANTHROPIC_API_KEY` / Bedrock / Vertex settings, so it always uses your
  subscription. Its error output is drained (a full pipe could hang a reply), Stop ends the whole process tree,
  and two Blenders no longer share one MCP config file.
- `add` in Edit Mode put the new geometry inside the edited mesh; batches now switch to Object Mode first.
- A taken name silently became `Box.001` so later ops hit the wrong object: `add` with an existing name updates it,
  and renames/duplicates refuse taken names.
- Read-only batches pushed undo steps (wiping redo); undo steps now have readable names (`Claude: add Sofa, mat +3`).
- `dims` scaled the object, so bevels were uneven and procedural textures stretched on big objects (the "sand dune"
  wood floor). Sizes are now baked into unshared meshes, and `tex` compensates for scale elsewhere. `dims` with a
  zero axis (planes) was ignored.
- Parenting an object right after moving it in the same batch reset its position.
- `render` permanently changed the engine, resolution and output path; settings are now restored.
- `look` changed the scene's JPEG quality setting.
- Values are converted by property type: degrees for every angle property, colours of the right length and colour
  space, any datablock by name, enum case. Rotations respect quaternion/axis-angle modes; `aim` works on parented
  objects; `mat`/`tex` repair materials whose Principled BSDF isn't connected.
- Big pasted images are downscaled before sending; chat errors are explained ("logged out", "usage limit", …).
- The MCP server path is absolute even when Blender was started via a relative path (macOS).
- Every version string now matches (the 0.1.2 zip reported 0.1.0).

### Added
- **Ops (28 → 45):** `place`, `check`, `api`, `world`, `edit`, `nodes`, `img`, `asset` (Poly Haven), `curve`,
  `room`, `fit`, `col`, `rename`, `key`, `anim`, `con`, `gameprep`; `look{views}` grid; `render{preview,look}`;
  `export{preset}`; `import{fit,col}`; `dup{tree}`; `mod{inputs}` for Geometry Nodes; `col` on add/dup/cam.
- **Chat:** Build / Plan first / Coach modes; a "what changed" line with *Select changed* and *Revert reply*;
  scene context line; live text streaming; usage footer and *Continue in a fresh chat*; effort picker; starters,
  templates, recipes (save/replay with zero tokens) and a bundled demo scene; Inbox import; lessons with local
  checks; teach-mode hints; optional resident CLI process (experimental).
- **Setup check** with Install / Log in / Update / Start buttons, and *Copy diagnostics*.
- **Preferences page:** port, autostart, default model and effort, CLI path, chats and Inbox folders, standing
  instructions, safe mode, teach mode, resident process.
- **Claude Code:** one-click *Connect Claude Code* with status, skill-outdated warning, a Claude Code plugin
  marketplace in the repo, and MCP `instructions`.
- **Distribution:** CI on Windows, macOS and Linux (Blender 4.2 LTS and 5.2); tagged releases build the zip and
  publish a Blender extension repository on GitHub Pages for in-app updates.

### Changed
- The chat uses its own Blender-only system prompt (`--system-prompt-file`) instead of appending to Claude Code's
  coding-agent prompt: a few thousand fewer tokens per call. The tool schema grew to ~1.3k tokens for 45 ops.
- `check` support detection looks under the whole footprint and ignores parts of one asset and room surfaces.

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
