---
name: blender-bridge
description: Build and edit scenes in the user's live Blender through the blender-bridge MCP tools (`bl` op batches, `look` captures). Use whenever the user asks to model, arrange, light, texture, frame, render or otherwise change something in Blender.
---

# Working in Blender with blender-bridge

Two tools: `mcp__blender-bridge__bl` runs a list of ops as ONE undo step; `mcp__blender-bridge__look` returns a small
JPEG. Every op replies with one short line, so trust the replies and keep image checks rare.

## Workflow

1. Unknown scene → start with `{"op":"scene"}`. Don't `new` (clear) unless the user asked for a fresh scene.
2. Plan the whole thing, then send it in **1–3 big batches** (20–40 ops each is fine), not one op per call.
3. Read the reply lines. On `i:op ERR ...` the ops before `i` were applied; fix and send only the rest.
4. Run `check`, fix what it reports, then verify with **one** `look` (`cam:true` if a camera exists, else `frame:"all"`), `max` 384. Fix, look again only if
   something was visibly wrong.
5. Reply in 1–3 sentences. The user sees the viewport.

## Getting it right first time

- Units are meters. Real sizes: door 2.1 m, table 0.75 m high, chair seat 0.45 m, sofa 2 × 0.9 × 0.8 m, room 3–5 m.
- Size boxes with `dims:[x,y,z]` (exact world size), not `scale`. Vectors always have 3 numbers.
- **Sit objects on things with `place`**, not by computing z: `place{obj:"Sofa",on:"floor"}`,
  `place{obj:"Mug",on:"Table",at:"center"}`, `place{obj:"Shelf",against:"Wall_N",side:"-y"}`. It uses the real
  rotated, bevelled bounds including children. Floor top is z = 0.
- Before the final `look`, run `check` (floating, overlapping, cut-off, missing lights/camera) and fix what it lists.
- Walls: thin boxes (0.1 m) placed at the floor edge, with z = wall height / 2.
- Materials: `mat:{"name":"Oak","color":"#a0703c","rough":0.6}` on `add`; reuse later as `mat:"Oak"`.
  Metals: `metal:1, rough:0.2–0.4`. Glass: `transmission:1, rough:0`. Emissive: `emit:"#ffcc88", emit_strength:5`.
- **Texture surfaces** instead of flat colours: `tex{obj,kind:"wood",color1,color2,scale}` for floors and tables,
  `fabric` for sofas and rugs, `marble`/`tiles` for counters, `brick`/`concrete` for walls, `leather` for chairs.
  Apply it after the object has its material; one `tex` per material is enough (shared materials share it).
- **Detail**: bevel every hard-surface box (`width` 0.005–0.02, `segments` 3); soft furniture (cushions, sofa) gets
  `subsurf` levels 2 with a bevel or a slightly smaller inner box so it doesn't melt into a blob; `smooth:true` on
  round things. Cylinders: `args:{vertices:48}` for smooth curves.
- Name every object meaningfully (`Sofa_Seat`, `Table_Leg_1`); parent parts with `set{parent}` for grouping, and put
  each asset in its own collection (`col:"Sofa"` on `add`/`dup`/`cam`, or `col{name,obj}`). Tidy names with
  `rename{obj,seq:"Leg_{n}",sort:"x"}`.
- Round, swept or turned shapes: `curve` — pipes/cables/rails `curve{pts,bevel:0.02,type:"smooth"}`, vases, bottles,
  lamp shades and turned legs `curve{lathe:[[r,z],...]}` (profile spun around Z). Much better than stacked cylinders.
- Downloaded or imported models: `import{path,fit:{height:1.8}}` or `fit{obj,height|longest}` afterwards: real size,
  sitting on the floor.
- Repeated parts: `dup{obj,n,by}` or `mod{type:"array"}`; symmetric parts: `mod{type:"mirror"}`.
- Rounded look: `mod{type:"bevel",props:{width:0.02,segments:3}}`; smooth organic: `subsurf` + `smooth:true`.
- Cut holes: `mod{type:"bool",props:{object:"Cutter",operation:"DIFFERENCE"}}`, then `set{obj:"Cutter",hide:true}`.

## Lights and camera

- Point/spot energy is in watts: lamp 50–150, room fill 300–800. Sun energy 2–5. Area 100–400.
- Warm `#ffd8a8`, neutral `#ffffff`, cool `#cfe0ff`. Aim spots/areas with `aim{obj,at}`.
- Set the world early, so `look` shows real lighting: interiors `world{hdri:"interior",strength:0.5}` plus area
  lights; outdoors `world{sky:true,sun:true}`; product shots `world{hdri:"studio",bg:"#ffffff"}`.
- Camera: `cam{loc,at,lens}`. Interior 18–28 mm from a corner at eye height (1.5–1.7 m); product 50–85 mm.
- Check framing with `look{cam:true}`; if things are cut off, move the camera back or lower the lens.

## Viewport for the user

`view{frame:"all"|"sel"|name, preset:"front"|"top"|..., orbit:[yaw,pitch], shading:"material"}` moves the user's own
view. Use `shading:"material"` after adding materials so they see colours.

## Escape hatches

- Any operator: `bop{id:"mesh.subdivide", obj, edit:true, args:{number_cuts:2}}`.
- Any property: `prop{path:"scene.render.engine", value:"CYCLES"}`, read with `get{path}`.
- `py{code}` only when no op fits; set `out` to return a value. Keep it short.

## Seeing several angles

`look{views:["cam","front","right","top"],max:512}` returns one grid image (tile order in the reply) and leaves the
user's view alone. Prefer it over several single looks when checking proportions. `render{preview:true,look:true}`
shows the real render engine's result without changing the user's render settings.

## Don'ts

- Don't `look` after every batch, and don't use `max` above 512 unless the user needs detail.
- Don't render (`render`) unless asked; it can take minutes.
- Don't delete or clear objects the user made unless asked.
