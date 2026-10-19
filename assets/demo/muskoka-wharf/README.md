# Muskoka Wharf photo examples

Night photographs taken by the project maintainer at Muskoka Wharf, with
AI-assisted edits created in Codex using Snapshot Realism.

This folder shows two parts of the workflow:

- **Script output:** each original processed directly by `realism.py`. These
  comparisons isolate the finishing script.
- **Snapshot Realism / AI-assisted edit:** the photo edits made during the
  project's demo work in Codex. These pairs show the skill's editing workflow.

The original photos and completed edits are retained unchanged. Comparison
sheets arrange each pair with labels and source credit.

## Script output on the originals

The originals went through the script with `--origin camera --preset night
--seed 42`. Camera origin is declared by the user; the script does not verify it.
Run from the repository root:

```bash
python skill/snapshot-realism/scripts/realism.py assets/demo/muskoka-wharf/plaza-before.jpg --origin camera --preset night --seed 42 -o plaza-script.jpg
python skill/snapshot-realism/scripts/realism.py assets/demo/muskoka-wharf/sculpture-before.jpg --origin camera --preset night --seed 42 -o sculpture-script.jpg
```

The commands write `plaza-script.jpg` and `sculpture-script.jpg` in the current
folder; the copies in this folder were made the same way. Script version: 0.5.0.

### Night plaza

[![100% crops of the original and the script output, night plaza](plaza-script-crops.jpg)](plaza-script-crops.jpg)

- [Original photo](plaza-before.jpg): attachment `1-1000029642.jpg`.
- [Script output](plaza-script.jpg).
- [Crop sheet](plaza-script-crops.jpg): 100% crops, original on the left and script output on the right.

### Bird sculpture

[![100% crops of the original and the script output, bird sculpture](sculpture-script-crops.jpg)](sculpture-script-crops.jpg)

- [Original photo](sculpture-before.jpg): attachment `3-1000029643.jpg`.
- [Script output](sculpture-script.jpg).
- [Crop sheet](sculpture-script-crops.jpg): 100% crops, original on the left and script output on the right.

## Snapshot Realism photo edits

These pairs show the original photographs alongside the AI-assisted edits made
in Codex using Snapshot Realism. The edits remove intrusive light streaks and
change some scene detail, including foliage. They illustrate the editing
workflow; the script-only comparisons above show capture processing separately.

### Night plaza

[![Original photo and Snapshot Realism AI-assisted edit, night plaza](plaza-comparison.jpg)](plaza-comparison.jpg)

- [Original photo](plaza-before.jpg): attachment `1-1000029642.jpg`.
- [Snapshot Realism AI-assisted edit](plaza-after.jpg): attachment `2-1000029650.jpg`.

### Bird sculpture

[![Original photo and Snapshot Realism AI-assisted edit, bird sculpture](sculpture-comparison.jpg)](sculpture-comparison.jpg)

- [Original photo](sculpture-before.jpg): attachment `3-1000029643.jpg`.
- [Snapshot Realism AI-assisted edit](sculpture-after.jpg): attachment `4-1000029651.jpg`.

## How to read these examples

The AI-assisted pairs show the completed edits. They do not isolate the finishing
script, so scene changes should be attributed to the editing stage. The two
script-only pairs provide the separate view of `realism.py`.

See [image rights and credits](../IMAGE-LICENSES.md).
