# Contributing

Contributions are welcome. You can fork it, modify it, extend it, or use it in your own projects (under the
GPL-3.0-or-later terms). Pull requests of any size are appreciated, from typo fixes to new ops.

## Ways to help

- **New ops**: rigging/armatures, sculpt helpers, UV editing beyond box projection, particles/physics…
- **Lessons and recipes**: a lesson is a small JSON file in `claude_bridge/lessons/`; a recipe is a saved chat in
  `claude_bridge/recipes/`. Both are easy first contributions.
- **Better defaults**: anything that makes models get it right on the first try (clearer error messages,
  forgiving argument shapes; see how `v3()`, `_coerce()` and the `mat` color shorthand work in `ops.py`).
- **Platform support**: Ctrl+V clipboard paste inside the text field on macOS/Linux, real-world testing there.
- **Chat UX**: image thumbnails, keyboard shortcuts, localization.
- **Bug reports**: open an issue with your Blender version, OS, model, and the chat or `bl` batch that failed.

## Development setup

```bash
git clone https://github.com/JackLuciano/claude-bridge-blender
cd claude-bridge-blender
python install.py          # links the addon into Blender, so your edits apply after a Blender restart
```

## Where things live

| File | What |
|---|---|
| `claude_bridge/__init__.py` | bridge socket (token, safe-mode approvals), preferences, Bridge panel |
| `claude_bridge/ops.py` | op registry, batch runner, core ops (add/set/mat/tex/mod/view/look/check/place/api/render…) |
| `anim.py`, `build.py`, `meshedit.py`, `nodes.py`, `assets.py`, `room.py`, `game.py` | more ops, by topic |
| `claude_bridge/chat.py` | in-Blender chat: CLI process, events, turn ledger, modes, templates, panel |
| `doctor.py`, `recipes.py`, `coach.py`, `teach.py` | setup check, recipes, lessons, teach-mode hints |
| `claude_bridge/mcp_server.py` | stdlib MCP server (two tools) |
| `claude_bridge/skill/SKILL.md` | the skill; `plugin/skills/blender-bridge/SKILL.md` must stay an exact copy |

## Adding an op

1. Add a function decorated with `@op("name")` in `claude_bridge/ops.py` or the topic module (`ro=True` if it never
   changes data). It takes the args dict and returns **one short line** (keep replies token-cheap; use
   `summary(o)` for objects).
2. Raise `ValueError` with a message a model can act on ("'loc' needs 3 numbers…") instead of letting bpy
   errors through. Use `_coerce()` when setting RNA properties from JSON.
3. Add the name to `OP_NAMES` and a one-line signature to `BL_DESC` in `claude_bridge/mcp_server.py`
   (the integration test fails if `OP_NAMES` and the ops drift apart, or the schema grows past 6000 chars).
4. Add a test in `tests/test_ops.py`. If the op is risky (runs code, deletes, touches files), extend
   `describe_risky()` so safe mode asks first.

## Tests

```bash
python run_tests.py                     # headless, required to pass (CI runs it on Windows, macOS and Linux)
python tests/live_smoke.py              # with Blender open: viewport, look, render (add --chat for real Claude requests)
blender --factory-startup --enable-event-simulate --python tests/live_panel.py   # screenshots of the chat panel
```

The chat pipeline is tested end to end with a stand-in CLI (`tests/fake_claude/`), so no account is needed.
Set `CLAUDE_BRIDGE_NET=1` and run Blender with `--online-mode` to include the Poly Haven download test.

## Releasing

Bump the version in `claude_bridge/blender_manifest.toml`, `bl_info`, `pyproject.toml`, `server.json` (twice),
`plugin/.claude-plugin/plugin.json` and `mcp_server.__version__` (a test checks they match), add a CHANGELOG
section, then push a `vX.Y.Z` tag: CI builds the zip, attaches it to the release and updates the extension
repository on GitHub Pages.

## Pull requests

- Keep PRs focused; describe what changed and how you tested it.
- Code comments in English. Match the surrounding style.
- Don't grow the tool schema without a good reason: every byte is sent on every request.
- By contributing you agree your work is licensed under GPL-3.0-or-later.
