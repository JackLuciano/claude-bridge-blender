# Blender Extensions listing: Claude Bridge

Copy these fields into the extensions.blender.org submission form.

---

## Name
Claude Bridge

## Tagline (from the manifest, max 64 chars)
Chat with Claude to build and edit your scene

## Support
https://github.com/JackLuciano/claude-bridge-blender/issues

## Website
https://github.com/JackLuciano/claude-bridge-blender

## Tags
User Interface, Development, 3D View

## License
GPL-3.0-or-later

---

## Description

**Chat with Claude right inside Blender: describe what you want, and it builds, edits and checks your scene while you
watch.**

Claude Bridge adds a **Claude** tab to the 3D View sidebar. Type a request like *"a cozy living room with a sofa, a
coffee table and a warm floor lamp"* and Claude places the objects at real-world sizes, assigns materials, sets up
lights and a camera, then looks at the result through your viewport and fixes anything that's off.

### Features

- **Chat in the sidebar**: replies stream in live, and every action Claude takes is shown inline.
- **Real edits, one undo step each**: each batch of changes is a single Ctrl+Z.
- **Show, don't describe**: attach a viewport capture, a full window screenshot or an image file, or paste one with
  **Ctrl+V**.
- **Saved chats**: reopen any previous conversation and Claude continues with its full context.
- **Model choice**: Opus (default), Sonnet or Haiku.
- **Works with Claude Code too**: one click copies the setup command, and another installs a skill, so Claude Code in
  your terminal can drive the same Blender.

### Requirements

- A Claude subscription (Pro or Max) and [Claude Code](https://claude.com/claude-code) installed and logged in. The
  chat runs the local `claude` command under your own login, so no API key is needed.
- Blender 4.2 or newer (tested on 5.2, Windows 11).

### Privacy and permissions

- **Network:** the add-on listens on **127.0.0.1 only** (port 9877) so the local Claude process can send it
  commands. Nothing is exposed to other machines.
- **Files:** viewport captures and renders are written to your temp folder; chats are saved in Blender's config
  folder.
- What you type and the images you attach are sent to Anthropic by Claude Code, the same as when you use Claude Code
  directly. The add-on itself sends nothing anywhere.

### Getting started

1. Install the extension and open the 3D View sidebar (**N**), then the **Claude** tab.
2. Type a request and press Enter.
3. Optional: in **Claude ▸ Bridge**, click **Copy Claude Code setup command** and run it once in a terminal, then
   click **Install Claude Code skill**.

Open source (GPL-3.0). Issues and pull requests are welcome on GitHub.

---

## Release notes: 0.1.2 (submit this version)

**Fixed**
- Text results (e.g. a shading mode name) no longer make an operation fail.
- Claude now sees your scene's real lighting when it checks its work, so lighting changes can be verified.
- The chat panel always shows the newest reply in long conversations.
- Pasting an image with Ctrl+V no longer briefly freezes Blender.

**Added**
- Procedural textures (wood, fabric, marble, brick, tiles, leather, concrete and more) that Claude can apply in one
  step, for much better-looking materials.

**Changed**
- Cleaner previews for Claude (no grid or gizmos) and a friendlier tagline.

## Release notes: 0.1.1

**Fixed**
- Two Blender windows could share the bridge port on Windows, so commands could reach the wrong Blender. The bridge
  now binds its port exclusively.

**Changed**
- A second Blender automatically uses the next free port (9878, …). Its chat follows that port, and the Bridge panel
  shows which Blender Claude Code is connected to.

## Release notes: 0.1.0 (if submitting the first version)

First release.
- Chat with Claude in the 3D View sidebar, with Opus by default and Sonnet or Haiku selectable
- Builds and edits scenes with compact operations; each batch is one undo step
- Image attachments: viewport capture, window screenshot, image file, Ctrl+V paste
- Saved and resumable chats
- Works with Claude Code via the local bridge, plus a bundled `blender-bridge` skill

---

## Images

| Field | File | Size |
|---|---|---|
| Icon | `icon.png` | 256 × 256 |
| Featured image | `featured.png` | 1920 × 1080 |
| Preview 1 | `preview_1.png` | "Describe a scene: Claude builds it in your viewport" |
| Preview 2 | `preview_2.png` | "Keep chatting: edits land as single undo steps" |
| Preview 3 | `preview_3.png` | "Attach a screenshot or paste with Ctrl+V" |

All images are generated from a real session by `tools/listing_assets.py` (captures) and
`tools/make_listing_images.py` (composition).
