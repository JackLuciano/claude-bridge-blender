"""Recipes: the op batches of a chat, saved as a small JSON file that rebuilds the scene with zero tokens.

    {"format": "claude-bridge-recipe", "version": 1, "title": "...", "prompts": [...], "batches": [[ops], ...]}
"""

import json
import os

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(ADDON_DIR, "recipes")
RISKY = {"py", "open", "save", "export", "import", "render"}  # run code or touch files outside the scene


def from_chat(state):
    prompts = [t for r, t in state["history"] if r == "user"]
    batches = [b["ops"] for b in state.get("batches", []) if b.get("ok") and b.get("ops")]
    # Read-only batches (scene/info/look-style) add nothing to a replay.
    from .ops import READ_ONLY, _normalize
    batches = [b for b in batches if any(_normalize(o).get("op") not in READ_ONLY for o in b)]
    if not batches:
        raise ValueError("this chat has no successful scene changes to save")
    return {"format": "claude-bridge-recipe", "version": 1, "title": (prompts[0] if prompts else "Recipe")[:80],
            "prompts": prompts, "batches": batches}


def save(recipe, path):
    if not path.endswith(".json"):
        path += ".bridge-recipe.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recipe, f, ensure_ascii=False, indent=1)
    return path


def transcript_md(state):
    names = {"user": "**You**", "claude": "**Claude**", "tool": "`tool`", "error": "*error*", "info": "*info*",
             "diff": "*changed*", "hint": "*how-to*"}
    return "\n\n".join(f"{names.get(r, r)}: {t}" for r, t in state["history"]) + "\n"


def load(path):
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    if not isinstance(r, dict) or r.get("format") != "claude-bridge-recipe" or not isinstance(r.get("batches"), list):
        raise ValueError("not a Claude Bridge recipe")
    return r


def describe(recipe):
    """Counts and risky ops, shown before a replay."""
    from .ops import _normalize
    ops = [_normalize(o).get("op") for b in recipe["batches"] for o in b]
    risky = sorted({o for o in ops if o in RISKY})
    return f"{len(recipe['batches'])} batches, {len(ops)} ops" + (f"; uses {', '.join(risky)}" if risky else "")


def replay(recipe, allow_risky=False):
    """Run every batch as its own undo step. Risky ops are refused unless allowed."""
    from . import _handle_request
    from .ops import _normalize
    if not allow_risky:
        bad = sorted({_normalize(o).get("op") for b in recipe["batches"] for o in b} & RISKY)
        if bad:
            raise ValueError(f"recipe uses {', '.join(bad)}; tick 'Allow' to run it")
    done = 0
    for i, b in enumerate(recipe["batches"]):
        r = _handle_request({"ops": b})
        if not r["ok"]:
            err = [ln for ln in r["text"].splitlines() if "ERR" in ln]
            raise ValueError(f"batch {i + 1} failed: {err[0] if err else r['text'][:200]}")
        done += 1
    return f"replayed '{recipe.get('title', 'recipe')}': {done} batches, zero tokens"


def bundled():
    try:
        return sorted(f[:-5] for f in os.listdir(BUNDLED) if f.endswith(".json"))
    except OSError:
        return []


def replay_bundled(name):
    return replay(load(os.path.join(BUNDLED, f"{name}.json")))
