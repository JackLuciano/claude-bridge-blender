# Claude Bridge for Blender

[![Latest release](https://img.shields.io/github/v/release/JackLuciano/claude-bridge-blender?label=download)](https://github.com/JackLuciano/claude-bridge-blender/releases/latest)
[![Tests](https://github.com/JackLuciano/claude-bridge-blender/actions/workflows/test.yml/badge.svg)](https://github.com/JackLuciano/claude-bridge-blender/actions/workflows/test.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange)

**[⬇ Download the latest release](https://github.com/JackLuciano/claude-bridge-blender/releases/latest)**: install the
zip via *Edit ▸ Preferences ▸ Get Extensions ▸ Install from Disk*.

![Demo: one chat message builds a textured reading nook in Blender](docs/demo.gif)

<!-- mcp-name: io.github.JackLuciano/claude-bridge-blender -->

Drive a live Blender with Claude, **from a chat panel inside Blender** or **from Claude Code**, using
compact, high-level ops instead of throwaway Python scripts. Runs on your normal Claude subscription
(Pro/Max) through the Claude Code CLI. No API key is needed.

```
 Blender sidebar chat ──spawns──▶ claude CLI (headless, your login) ─┐
                                                                     ├─ MCP stdio ─▶ mcp_server.py ─TCP 127.0.0.1:9877─▶ addon ─▶ bpy
 Claude Code (terminal / desktop) ───────────────────────────────────┘
```

## What's new in 0.2

The biggest update so far: 45 ops (up from 28), three chat modes, and many fixes. Full list in the
[CHANGELOG](CHANGELOG.md).

**Fixes you'll notice**
- 🔒 **Security:** a web page could reach the bridge port and run code in Blender. The bridge now refuses anything
  that isn't from the MCP server (per-launch token, HTTP requests rejected).
- 💳 **Always your subscription:** an `ANTHROPIC_API_KEY` in your environment is no longer passed to the chat's CLI.
- 🪑 **Edit Mode trap fixed:** `add` no longer puts new geometry inside the mesh you were editing, and a taken name
  no longer silently becomes `Box.001`.
- 🪵 **Real-world sizes:** `dims` is baked into the mesh, so bevels and textures look the same on every object
  (the stretched wood grain on big floors is gone).

**Building better scenes**
- 📐 **`place` and `check`:** rest objects exactly on the floor, a table or against a wall; `check` lists floating,
  overlapping, cut-off or unlit things as text, with a fix for each.
- 🖼 **Multi-view `look`:** camera + front + side + top in one image. **`render`** can now show Claude its own
  render and no longer changes your render settings.
- 🌍 **`world`:** studio/interior/outdoor HDRIs bundled with Blender, a sky with a matching sun, or a flat colour.
- 🧱 **`edit`:** extrude, inset, bevel and loop-cut faces picked by direction, material or your selection.
- 🕸 **`nodes`:** shader, world, compositor and Geometry Nodes graphs in one compact op, plus a scatter preset.
- 🪵 **Free photo textures, HDRIs and models from [Poly Haven](https://polyhaven.com)** (`asset`), and your own PBR
  texture folders (`img`), at real-world scale without UVs.
- 🌀 **`curve`** (pipes, cables, lathed vases and legs), **`room`** (walls with doors and windows from a floor plan),
  **`fit`** (normalize imported models), **`col`** and **`rename`** (tidy scenes).
- 🎬 **Animation:** `key`, a one-line turntable, spin/pop-in/bounce presets, constraints (`con`).
- 🎮 **Game assets:** `check{only:["game"]}`, `gameprep` (UVs, transforms, LODs, collision) and Unity/Unreal/Godot
  export presets.

**A better chat**
- 🧭 **Modes:** *Build*, *Plan first* (tick the steps you want; applying costs no tokens) and *Coach* (Claude can
  look but not touch, with lessons that are checked locally).
- ↩️ **What changed + Revert:** every reply ends with `+added ~changed −removed`, *Select changed* and a one-click
  *Revert reply*.
- 🎯 **Scene context:** "make *this* red", "put a lamp *here*" just work (selection, mode and 3D cursor are sent
  along, ~30 tokens).
- ⚡ **Fewer tokens:** a Blender-only system prompt replaces Claude Code's coding prompt; a usage footer shows the
  context size, and long chats can continue in a fresh chat that carries the scene over.
- 🧩 **Starters, templates and recipes:** one-click starters, *Critique*/*Light this*/*Tidy up* templates, save a
  chat as a recipe that rebuilds the scene with zero tokens, and a demo scene that works before you log in.
- 🩺 **Setup check:** missing, logged-out or outdated CLI is detected with fix buttons; *Copy diagnostics* for bug
  reports.
- ⚙️ **Preferences:** port, default model and effort, CLI path, folders, standing instructions, safe mode (approve
  risky ops), teach mode (how-to hints), live streaming and an optional resident CLI process.
- 🍎🐧 **macOS and Linux:** the CLI is found even when Blender starts from the Dock; CI runs on all three systems.

## Features

- **In-Blender chat** (View3D ▸ Sidebar ▸ **Claude**): replies stream in live, tool calls are shown inline, and
  Claude changes the scene while you watch. Each reply ends with a summary of what changed and a Revert button.
- **Three modes**: Build, Plan first, Coach (see above). Model picker (Opus default, Sonnet, Haiku, CLI default)
  and effort picker.
- **Chat history**: every chat is saved. Pick an old one from the dropdown to reopen it; the Claude session
  resumes with its full context.
- **Screenshots and images**: attach a clean viewport capture, the whole Blender window, an image file, or
  **paste with Ctrl+V** (works even while typing in the chat field; Windows). Big images are downscaled first.
- **Inbox**: drop GLB/FBX/OBJ files into the Inbox folder and import them at a sensible size with one click.
- **Built-in skill** ([`claude_bridge/skill/SKILL.md`](claude_bridge/skill/SKILL.md)): workflow and real-world
  modelling tips. It's part of every in-Blender chat, and installed for Claude Code as the `blender-bridge` skill.
- **Token-lean by design**:
  - The chat runs the CLI with no built-in tools, skills, other MCP servers or user hooks, and its own short
    system prompt.
  - Two MCP tools with a ~1.3k-token schema covering 45 ops.
  - Each op reply is one line, a batch is one call and one undo step, and captures are small JPEGs.

## Why ops instead of scripts

| | Script-based bridge | Claude Bridge |
|---|---|---|
| Typical action | 200–600 tokens of Python | `{"op":"move","obj":"Box","by":[0,0,1]}` |
| Result | free-form prints / tracebacks | `Box[mesh] @(0,0,1) dim(2,2,2) mat:Wood` |
| Verification | separate query call | built into every op's reply, plus `check` |
| Many changes | many calls | one batch = one call = **one undo step** |
| Seeing the scene | full screenshots | `look`: 256–512 px JPEG, several angles in one image |

Python is still there as a last resort (`py` op), and `api` looks up operator arguments and node sockets in your
Blender so Claude doesn't have to guess.

## Install

Requirements: Blender 4.2+ (tested on 4.2 LTS and 5.2), and [Claude Code](https://claude.com/claude-code) installed
and logged in (`claude` once in a terminal). Python 3.10+ only for installing from source.

### From a release (easiest)

1. Download `claude_bridge-x.y.z.zip` from [Releases](https://github.com/JackLuciano/claude-bridge-blender/releases).
2. In Blender, go to **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…** and pick the zip.
3. The in-Blender chat works right away. The setup check tells you if the CLI is missing or logged out.
4. **For Claude Code** (optional): in **Sidebar ▸ Claude ▸ Bridge**, click **Connect Claude Code**, then restart
   Claude Code.

**Automatic updates:** add `https://jackluciano.github.io/claude-bridge-blender/index.json` under
*Preferences ▸ Get Extensions ▸ Repositories ▸ +* and Blender will offer new versions.

### As a Claude Code plugin

```bash
claude plugin marketplace add JackLuciano/claude-bridge-blender
```

```bash
claude plugin install blender-bridge@claude-bridge-blender
```

This adds the MCP server (via `uvx claude-bridge-blender`) and the skill. You still need the add-on running in
Blender.

### From source

```bash
git clone https://github.com/JackLuciano/claude-bridge-blender
cd claude-bridge-blender
python install.py
```

`install.py` links `claude_bridge/` into Blender's user add-ons (use `--copy` to copy instead), enables it,
registers the MCP server with Claude Code as `blender-bridge`, and installs the `blender-bridge` skill
(skip the Claude Code steps with `--no-mcp`).

Start Blender and open the **Claude** tab in the 3D view sidebar (press `N`). The bridge starts automatically.

- **In Blender:** type in the chat field and press Enter, or click a starter.
- **In Claude Code:** just ask, e.g. *"make a low-poly table with a wooden material, then show me"*.

## Chat panel

| Control | What it does |
|---|---|
| Chat dropdown / ➕ / 🗑 | switch between saved chats, start a new one, delete the current one |
| Text field + ▶ | send (Enter also sends); ✖ stops a running reply |
| Select changed / Revert reply | select what the last reply touched / undo the whole reply in one click |
| 🧊 / 🪟 / 🖼 / 📋 | attach a viewport capture / the Blender window / an image file / the clipboard (or **Ctrl+V**) |
| 📥 Inbox | import new models dropped into the Inbox folder (Shift+click opens it) |
| ☰ Templates | ready-made requests, *Save as recipe*, *Replay recipe* |
| 📄 Copy | copy the whole chat |
| Mode · Model · Effort | Build / Plan first / Coach · Opus / Sonnet / Haiku / CLI default · reasoning effort |
| Footer | context size and the last reply's steps, tokens and time; ⧉ continues in a fresh chat |

Chats are stored as JSON under Blender's config folder (`…/config/claude_bridge/chats/`, configurable in
Preferences).

## MCP tools

### `bl`: run a batch of ops

```json
{"ops": [
  {"op": "new"},
  {"op": "room", "pts": [[0,0],[4,0],[4,3],[0,3]], "openings": [{"wall": 0, "at": 1, "w": 0.9}]},
  {"op": "add", "type": "cube", "name": "Top", "dims": [1.2, 0.7, 0.04], "col": "Desk",
   "mat": {"name": "Oak", "color": "#a0703c", "rough": 0.6}},
  {"op": "tex", "obj": "Top", "kind": "wood"},
  {"op": "place", "obj": "Top", "on": "floor"},
  {"op": "world", "hdri": "interior", "strength": 0.5},
  {"op": "check"}
]}
```

Ops run in order, stop at the first error (reported with its index), and the whole batch is one undo step.
`obj` is a name, a list of names, `"sel"` or `"active"`. Sizes are meters, angles degrees, colors `"#rrggbb"` or
linear `[r,g,b]`.

| Op | Args |
|---|---|
| `scene` | `filter`, `type`, `limit`: compact object list (numbered runs collapsed) |
| `info` | `obj`: transform, mesh stats, modifiers, light/camera data |
| `get` / `prop` | `path` (`scene.render.fps`, `Cube.data.energy`, `Cube.modifiers.Bevel.width`), `value` |
| `api` | `id` (operator args), `path` (writable props), `node` (sockets), `search`: look things up in this Blender |
| `add` | `type` (cube, sphere, ico, cylinder, cone, plane, circle, torus, monkey, grid, empty, text, curve, camera, point, sun, spot, area), `name`, `loc`, `rot`, `scale`, `dims`, `size`, `mat`, `color`, `col`, `smooth`, `body`, `energy`, `args` |
| `set` / `move` | absolute / relative transforms, `hide`, `parent`, `smooth`, `props{dotted.attr: val}` |
| `del`, `dup`, `select`, `join` | delete / duplicate (`n`, `by`, `linked`, `tree`) / select / join |
| `place`, `check` | exact contact (`on`, `at`, `against`, `side`, `gap`) / measured problems (`only`, `budget`) |
| `mat`, `tex`, `img`, `asset` | material / procedural texture / PBR image set / Poly Haven textures, HDRIs, models |
| `nodes` | `mat`\|`world`\|`gn`\|`comp`, `add`, `set`, `link`, `del`, `show`, `preset:"scatter"` |
| `mod` / `apply` | add or edit modifiers (incl. Geometry Nodes `inputs`), apply modifiers or transforms |
| `edit` | `sel` (direction, material, height, selection, ids) + `do` steps (extrude, inset, bevel, cut, …) |
| `curve`, `room` | curves, pipes and lathes / rooms with doors and windows |
| `fit`, `col`, `rename` | normalize imports / collections / batch renames |
| `key`, `anim`, `con` | keyframes / frame range and presets (turntable, spin, pop_in, bounce, float) / constraints |
| `aim`, `cam`, `view`, `world` | point at things, scene camera, drive the user's viewport, background and lighting |
| `bop` | any `bpy.ops` operator: `id`, `args`, `obj`, `edit`, `ctx: "view3d"` |
| `render` | `res`, `pct`, `samples`, `engine`, `preview`, `look` (return the image); settings are restored |
| `gameprep`, `export` | game-ready meshes / `preset: unity\|unreal\|godot`, `per: "object"` |
| `import`, `save`, `open`, `new`, `undo`, `redo` | files (`import{fit}`) and history |
| `py` | last-resort Python; assign `out` to return a value |

### `look`: see the result

Returns a small JPEG (default 512 px) rendered from the viewport with OpenGL, so it has no UI, popups or other
windows. `cam: true` gives the scene camera's view. `views: ["cam","front","right","top"]` returns one grid image
of several angles and leaves your view alone. View args (`preset`, `orbit`, `zoom`, `frame`, `shading`) are
applied first, so "orbit 45° and show me" is a single call.

## Tests

```bash
python run_tests.py                                    # headless: ops, chat (with a stand-in CLI), MCP end-to-end
python tests/live_smoke.py --chat                      # running Blender GUI: viewport, look, render, real chat, paste
blender --python tests/live_paste.py                   # Windows: real Ctrl+V keystroke into the chat
```

71 headless tests run on Windows, macOS and Linux in CI. `--chat` makes a few real Claude requests on your
subscription.

## Security

- The add-on listens on **127.0.0.1 only**. Every request must carry a per-launch token that only local processes
  of your user can read, and anything that looks like a web request is dropped.
- It executes the ops it receives, including `py`. Turn on **Safe mode** (Bridge panel or Preferences) to approve
  Python, scene clears, file opens, deleting your objects and file access outside the project/temp folders.
- The chat's Claude process can use only the two bridge tools: no shell, no file access. Coach and Plan modes
  are read-only, enforced by the bridge.
- Poly Haven downloads happen only when asked, only from Poly Haven, are checksum-verified, and respect
  Blender's *Allow Online Access* setting.

## Notes

- Ctrl+V paste: Windows works everywhere, including the text field. On macOS/Linux it works over the sidebar but
  not while editing the text field; use the 📋 button there. Clipboard reading uses `pngpaste`/AppleScript on
  macOS and `wl-paste`/`xclip` on Linux.
- Port: set it in Preferences (default `9877`; a second Blender takes the next free port). `CLAUDE_BRIDGE_PORT`
  sets it for the MCP server.

## Contributing

Feel free to fork, modify and improve it. Issues and pull requests are very welcome, from small fixes to new
ops, lessons and recipes. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, how to add an op, and the test commands.

## License

GPL-3.0-or-later (required for Blender add-ons that import `bpy`). Poly Haven assets are CC0.
