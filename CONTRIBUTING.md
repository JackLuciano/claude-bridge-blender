# Contributing

Contributions are welcome. You can fork it, modify it, extend it, or use it in your own projects (under the
GPL-3.0-or-later terms). Pull requests of any size are appreciated, from typo fixes to new ops.

## Ways to help

- **New ops**: geometry nodes, shader node graphs, UV, animation/keyframes, rigging, sculpt helpers…
- **Better defaults**: anything that makes models get it right on the first try (clearer error messages,
  forgiving argument shapes; see how `v3()` and the `mat` color shorthand work in `claude_bridge/ops.py`).
- **Platform support**: Ctrl+V clipboard paste inside the text field on macOS/Linux, testing on those platforms.
- **Chat UX**: rendering, markdown, image thumbnails, keyboard shortcuts.
- **Bug reports**: open an issue with your Blender version, OS, model, and the chat or `bl` batch that failed.

## Development setup

```bash
git clone https://github.com/JackLuciano/claude-bridge-blender
cd claude-bridge-blender
python install.py          # links the addon into Blender, so your edits apply after a Blender restart
```

## Adding an op

1. Add a function in `claude_bridge/ops.py` decorated with `@op("name")`. It takes the args dict and returns
   **one short line** (keep replies token-cheap; use `summary(o)` for objects).
2. Raise `ValueError` with a message a model can act on ("'loc' needs 3 numbers…") instead of letting bpy
   errors through.
3. Add the name to `OP_NAMES` and a one-line signature to `BL_DESC` in `claude_bridge/mcp_server.py`
   (the integration test fails if `OP_NAMES` and the ops drift apart).
4. Add a test in `tests/test_ops.py`.

## Tests

```bash
python run_tests.py                     # headless, required to pass
python tests/live_smoke.py              # with Blender open: viewport + look (add --chat for real Claude requests)
```

## Pull requests

- Keep PRs focused; describe what changed and how you tested it.
- Code comments in English. Match the surrounding style.
- Don't grow the tool schema without a good reason: every byte is sent on every request.
- By contributing you agree your work is licensed under GPL-3.0-or-later.
