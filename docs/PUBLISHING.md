# Publishing checklist

These steps need your own accounts, so they are done by hand. Everything they need is in the repo.

## GitHub repo settings (one-time)

- **Social preview:** go to Settings ▸ General ▸ Social preview ▸ Edit ▸ Upload, and pick
  [`docs/social-preview.png`](social-preview.png) (1280 × 640).
- **Pin the repo:** on your profile page, go to *Customize your pins* and tick `claude-bridge-blender`.

## Blender add-on distribution

- **extensions.blender.org: rejected** (2026-10-14). The platform doesn't host extensions that use or connect to AI
  models or paid services, so don't resubmit.
- **Own extension repository:** every `vX.Y.Z` tag publishes `https://jackluciano.github.io/claude-bridge-blender/index.json`
  (GitHub Pages, source: GitHub Actions; the `github-pages` environment allows `v*` tags). Users add it once and get
  updates inside Blender.
- **Marketplaces** (optional): text and images for Superhive, Gumroad or itch.io are in [`store-kit/`](store-kit/).
  Check each store's AI policy first.

## PyPI + MCP Registry (lists the server at registry.modelcontextprotocol.io)

The registry only lists servers published as a package. `pyproject.toml` builds a tiny package with just the MCP
server (`uvx claude-bridge-blender`). The README contains the `mcp-name:` marker the registry uses to verify you own
the PyPI package.

1. Create a PyPI account and an API token (pypi.org ▸ Account settings ▸ API tokens).
2. Build and upload:
   ```bash
   uv build --out-dir pkgdist
   uv publish pkgdist/*            # paste the token when asked (user: __token__)
   ```
3. Install the registry CLI (`mcp-publisher`) from
   https://github.com/modelcontextprotocol/registry/releases, then:
   ```bash
   mcp-publisher login github      # opens a browser; namespace io.github.JackLuciano
   mcp-publisher publish           # reads server.json in the repo root
   ```
4. On each release, bump `version` in `pyproject.toml`, `server.json` (both places) and
   `claude_bridge/blender_manifest.toml`, then repeat steps 2–3.

Other MCP directories (Glama, mcp.so, Smithery) mostly pick up registry and awesome-list entries automatically.
Glama also lists servers that have a GitHub repo, through its "Add server" page.

## Awesome lists

| List | Status |
|---|---|
| punkpeye/awesome-mcp-servers | PR opened: https://github.com/punkpeye/awesome-mcp-servers/pull/16350 |
| agmmnn/awesome-blender | PR opened: https://github.com/agmmnn/awesome-blender/pull/121 |
| hesreallyhim/awesome-claude-code | **Not yet eligible.** It needs 14+ days since the first commit with continued activity, or 100+ stars. Submit on or after **2026-10-27** using their issue form ("Recommend a resource"); they don't take PRs. Text below. |

### awesome-claude-code submission (from 2026-10-27)

- **Display name:** Claude Bridge for Blender
- **Category:** Tooling (or "Agent Skills" / "Integrations", whichever the form offers)
- **Link:** https://github.com/JackLuciano/claude-bridge-blender
- **Description:** Drives a live Blender from Claude Code through a token-lean MCP server: two tools, compact op
  batches (one undo step each) and small viewport captures, plus a bundled `blender-bridge` skill. It also adds an
  in-Blender chat panel that runs the local `claude` CLI headless on your subscription.
