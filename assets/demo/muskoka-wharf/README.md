# Muskoka Wharf photo examples

Night photographs supplied by the project maintainer. Each scene has an original
and a second, processed version that the maintainer also supplied. The original
attachments are copied byte-for-byte; the comparison sheets only arrange them
with captions for viewing.

The two kinds of file are different things and are labelled differently:

- **Script output**: made by running `realism.py` on the original, declared as a
  camera photo. This is the honest real-camera example.
- **AI-edited (as supplied)**: the processed files the maintainer supplied. They
  changed scene content, which only a generative edit does, and they carry no AI
  label.

## Script output on the originals

The originals went through the script with `--origin camera --preset night
--seed 42`. The script cannot verify that a file came from a camera, so the output
says "origin declared as camera, not verified". Run from the repository root:

```bash
python skill/snapshot-realism/scripts/realism.py assets/demo/muskoka-wharf/plaza-before.jpg --origin camera --preset night --seed 42 -o plaza-script.jpg
python skill/snapshot-realism/scripts/realism.py assets/demo/muskoka-wharf/sculpture-before.jpg --origin camera --preset night --seed 42 -o sculpture-script.jpg
```

The commands write `plaza-script.jpg` and `sculpture-script.jpg` in the current
folder; the copies in this folder were made the same way. Script version: 0.5.0.

### Night plaza

[![100% crops of the original and the script output, night plaza](plaza-script-crops.jpg)](plaza-script-crops.jpg)

- [Original as supplied](plaza-before.jpg): attachment `1-1000029642.jpg`.
- [Script output](plaza-script.jpg).
- [Crop sheet](plaza-script-crops.jpg): 100% crops, original on the left and script output on the right.

### Bird sculpture

[![100% crops of the original and the script output, bird sculpture](sculpture-script-crops.jpg)](sculpture-script-crops.jpg)

- [Original as supplied](sculpture-before.jpg): attachment `3-1000029643.jpg`.
- [Script output](sculpture-script.jpg).
- [Crop sheet](sculpture-script-crops.jpg): 100% crops, original on the left and script output on the right.

## AI-edited versions (as supplied)

The maintainer identified files 2 and 4 as the processed versions. Compared with
the originals, they changed scene content: on the plaza the light streak is gone
and foliage appears on the left; on the sculpture the horizontal light streak is
gone and detail in the dark trees changed. A script that only adds noise, white
balance, fringing, noise reduction and sharpening cannot do any of that. They are therefore
labelled **AI-edited (as supplied)** in the comparison sheets.

### Night plaza

[![Original and AI-edited version, as supplied, night plaza](plaza-comparison.jpg)](plaza-comparison.jpg)

- [Original as supplied](plaza-before.jpg): attachment `1-1000029642.jpg`.
- [AI-edited, as supplied](plaza-after.jpg): attachment `2-1000029650.jpg`.

### Bird sculpture

[![Original and AI-edited version, as supplied, bird sculpture](sculpture-comparison.jpg)](sculpture-comparison.jpg)

- [Original as supplied](sculpture-before.jpg): attachment `3-1000029643.jpg`.
- [AI-edited, as supplied](sculpture-after.jpg): attachment `4-1000029651.jpg`.

## How to read these examples

The AI-edited pairs do not isolate the finishing script. Their original editing
prompts, tools and settings were not recorded with the files, so do not attribute
the removed light streak or the added foliage to noise, white balance, sharpening
or JPEG compression.

The supplied files do not contain a processing declaration. Missing AI metadata
does not authenticate a camera photo, and that is also why the script accepts the
originals in camera mode: it found no AI marker, which proves nothing about where
they came from.

See [image rights and credits](../IMAGE-LICENSES.md).
