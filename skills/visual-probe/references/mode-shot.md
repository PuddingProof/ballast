# Mode: `shot` — see it

```
node $P shot <url|path> --matrix default     # navigate + capture the fidelity matrix
node $P shot <url|path> --crop <selector>    # capture + magnify ONE region
```

`--matrix` takes a preset (`default` | `quick` = 2-cell fast pass | `desktop`) or an inline
`WxH@DSF,WxH@DSF` list. A bare path is normalized to `file://`; remote origins need
`--allow-remote` (the guard is fail-closed). `--settle MS` adds a dwell between load and the
shutter, per cell — for a project whose entrance animation or theme crossfade finishes after load.

**One invocation carries all its cells.** Every `probe.mjs` call pays a full browser boot, so pass
comma-joined cells (`--matrix 1920x720@1,1920x720@1.5,1920x720@2`) instead of one invocation per
cell — measured per-cell runs spent most of their time re-booting.

## Plan the matrix before you shoot

A green matrix is only as strong as its worst-covered cell, and the defects that ship are the ones
no cell ever forced. So shoot the composed worst corner — every overlay OPEN × non-default theme ×
worst/empty/error content × the viewport extremes, composed *simultaneously*, because a bug needing
three stressors at once never shows when you vary one axis at a time.

Both the derivation rule and the mandatory native-resolution cell (`viewport.native`, which no
nearby size substitutes for) live in `state-contract.md`; the mechanical path to those states is
the `states` mode body.

## `--cdp` attach trades enumeration for the real compositor

Attaching to a running browser (`--cdp ws://127.0.0.1:<port>`) captures the true compositor's
RENDERING — the one thing a fresh headless launch cannot reach — but input stays synthetic, and
attach is single-cell: it sees only the state that window is already in. The states×fidelity
product exists only in fresh-launch enumeration, so use attach to confirm one real-compositor cell,
never to cover the matrix.

## Reading the output

Read per SKILL.md's capture discipline (manifest first, crops only). Two `shot`-specific notes:
manifest-driven states emit full frames only, so a suspect cell there earns a targeted `--crop`
recapture before it becomes filing evidence; and for an **animated** canvas, cross-cell divergence
Δ is partly temporal (a different frame per cell), not only scale — the magnified crop is ground
truth, not the Δ.
