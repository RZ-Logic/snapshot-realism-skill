---
name: snapshot-realism
description: Gives an image the look of an ordinary phone snapshot with a script that adds sensor noise, phone-style noise reduction and sharpening, color fringing and a neutral white balance, then saves a JPEG whose metadata states its declared origin (camera, edited or generated). Use it to finish a newly generated scene or an AI edit of a camera photo, to fix a genuine camera photo's imperfections (flare, light streaks, haze, dust) without adding grain, and to write the prompt for a candid generated scene. For fictional people, the user's own likeness, or people who gave permission. Never for hiding that an image is AI-generated.
license: MIT
compatibility: Needs code execution with Python 3.9+, numpy and Pillow. Without it only the prompt recipe works. Optional C2PA signing (--sign) also needs c2pa-python 0.38+ on Python 3.10+.
metadata:
  author: Rizwan Ahmed
  version: "0.6.2"
---

# Snapshot realism

Requirements: Python 3.9+ with numpy and Pillow. Without code execution, the prompt recipe still
works for writing a prompt, but the image will not get the script's processing or its origin label.
Signing (`--sign`, optional) also needs c2pa-python 0.38 or newer, which needs Python 3.10+.

## Scope

Use this skill for fictional people, the user's own likeness, or people who gave permission. When
the user supplies a real person as a reference, ask them to confirm it is themselves or someone who
consented before generating or editing. Any edit of a real person's likeness is `--origin generated`
(or `edited` if it is an inpaint on their camera photo), never `camera`. Never use this skill to hide
that an image is AI-generated or to pass a generated image off as a camera photo.

## Choose the path

| Situation | Path | `--origin` |
|---|---|---|
| A genuine camera photo | Fix only the camera imperfections the user names; add no capture effects by default (see below). | `generated` after an AI fix (`edited` for a masked inpaint), else `camera` |
| A camera photo with AI-generated parts (inpainting, object removal, generative fill) | Process the edited file directly. | `edited` |
| A newly generated scene, including one regenerated from a reference photo | Prompt recipe, generate, Gate A and Gate B, script, inspect, hand back. | `generated` |

Anything Codex `image_gen` or ChatGPT produced is `generated`, whatever the input check says. For an
image the user supplies, unless they said, ask whether it is a photo they took and whether an AI
tool changed it; never assume `generated`: appearance alone does not establish where it came from.

Always work on a copy in a writable folder; the script never overwrites the input. Capture strength
changes capture artifacts, not faces, hair, pose or scene structure.

For reference-guided generation, name the details the user wants kept, such as the same person's
identity, setting or requested outfit. Unless the user locks them, allow small natural changes in
posture, expression and hair to support a candid moment, along with requested skin or hair texture
changes. Exact identity preservation is not guaranteed: compare the result with the reference and
check for identity drift before accepting it.

## Newly generated scene

1. **Rewrite the request** with [references/prompt-recipe.md](references/prompt-recipe.md):
   capture, subject, moment, light, framing, artifacts, in that order. Drop its pure polish words
   (8k, masterpiece, photorealistic and the like), translate by intent the words that can carry a
   requirement (soft light, bokeh, beautiful, cozy and the like), and tell the user what you dropped
   and translated. For night, describe what a phone night shot looks like and never pair "iPhone"
   with "high ISO, grainy". Ask for one or two capture imperfections, not a full checklist. Name the
   setting specifically and apply the recipe's scene pass: no house plants or flowers unless asked,
   ending the prompt with "No house plants or flowers." Show the rewritten prompt.
2. **Generate** from the rewritten prompt, including supplied references for details that must carry
   over; portrait orientation for people unless asked. In Codex, use the built-in `image_gen` tool,
   passing the rewritten prompt as written: skip the imagegen skill's prompt augmentation and its
   "photorealistic-natural" polish, which push back toward the editorial look this skill removes.
   Without an image tool (for example in Claude Code), give the user the rewritten prompt, ask them
   to generate the image and supply the file, then continue from step 3.
3. **Check** the subject and the prominent background objects against Gate A and Gate B in the
   recipe, naming defects you can see (construction, contact shadows, materials), not tidiness. Any
   Gate A failure: regenerate (or crop it out if that leaves a usable image). Two or more Gate B
   tells, or one background tell the user asked to fix: regenerate with a new prompt that pushes
   harder on the failed slot. After 3 regenerations, a remaining Gate A defect means a "failed
   draft" that names the defects; do not run the script on it or present it as finished unless the
   user asks. If only Gate B tells remain, hand back the best attempt and name them. Say which tells
   you saw, and which background objects you checked, each time.
4. **Post-process** with `scripts/realism.py` (in this skill's folder; use its full path). Pick the
   preset from the scene's actual light level, not from the mood you want:

   ```bash
   python <skill-dir>/scripts/realism.py INPUT --origin generated --preset <day|indoor|night> --compare
   ```

   | Preset | Use for | JPEG quality | Sharpening |
   |---|---|---|---|
   | `night` | Night outdoors, bars, concerts | 85 | 0.30 |
   | `indoor` | Rooms, offices, evening | 88 | 0.40 |
   | `day` | Daylight | 90 | 0.45 |

   The default `--capture-strength` is 0.5 (0 for camera and edited photos); strength is independent of the
   preset (`1` stronger, `2` strong, `3` maximum): do not choose `night` for a stronger effect.
   Strength means how rough the capture was: it raises noise and noise reduction, sharpening reaches
   the table's value at 1 and no more, and color fringing stops growing at 1.5x. At `0` those
   capture effects are bypassed; white balance, warmth, resize, JPEG quality and origin still apply.

   If the user asked for warm light (candles, a lamp, sunset), add `--wb-strength 0` so the script
   does not neutralize it.

5. **Inspect** the processed image and the comparison at fit size and at 100% before handing back:
   crop native-resolution regions (face, hands, any text, a shadow area, a prominent background
   object) from both and view them side by side. If grain, smear or sharpening looks artificial,
   lower `--capture-strength` or keep the unprocessed image. Label a reference-guided variation as a
   generative change, and compare its raw image with its own processed output.
6. **Hand back** the processed JPEG, the compare sheet, the `capture strength:` line, the
   `white balance:` and `signed:` lines if printed, every `warning:` line, one line on any tell the
   script cannot fix, and an offer to rerun it stronger (`1`) if it looks too clean, or at `0` (no
   capture effects) if the grain looks wrong.

Finding the image:
- **Codex:** `image_gen` saves to `$CODEX_HOME/generated_images/<thread-id>/` (`~/.codex` by
  default). Copy the newest file there into the workspace and run the script on the copy, so
  output lands where the sandbox can write. If `import numpy` or `import PIL` fails, tell the
  user to run `python -m pip install numpy pillow` once; do not install packages yourself.
- **Claude.ai:** an upload is read-only. Copy it to a writable folder first, run the script on the
  copy, and write the result with `-o` into the outputs folder so the user can download it.
- **ChatGPT:** the image tool's output may not be visible to Python. If the script cannot find
  the image, ask the user to download it and upload it back into the chat.

## AI edit of a camera photo

Same command with `--origin edited`; like a camera photo it gets no capture effects or white balance
unless the user asks. Check the edited region for Gate A failures first. If the script warns of
camera metadata, keep `edited`.

## Existing camera photo: fix camera imperfections

A real photo already has real phone texture, so add none by default. Ask which imperfection to
fix; if none, leave the photo alone. The script cannot remove color fringing, noise or blur (for a
color cast, see white balance below). For those, or for flare, light streaks, ghosting, haze or
dust, make a conservative AI edit: "Remove the [imperfection]. Change nothing else: keep every
lamp, sign, word of text, sculpture and building exactly as it is." Without an image tool, give the
user that prompt and continue when they supply the file. Image models redraw signs, text and
fixtures, so compare them with the original at 100%. Then label the result without changing its
color, size or texture: `generated` when the model returned a whole new frame (Codex `image_gen`
and ChatGPT do), `edited` only for a masked inpaint that kept the other pixels:

```bash
python <skill-dir>/scripts/realism.py FIXED --origin generated --preset <day|indoor|night> --capture-strength 0 --wb-strength 0 --max-edge 8192 --quality 95
```

Add capture effects (`--origin camera --capture-strength 0.5`) only when the user wants a phone
look on an unedited photo, such as a polished DSLR shot.

Add `--keep-date` (camera and edited only) to carry over the three capture-time tags and nothing
else, never GPS, device or lens data; with `--origin generated` it is an argument error.

White balance is off by default for camera and edited photos, because the phone's own white balance
already ran; `--wb-strength 0.8` turns it on for a visible cast. Do not say this script
authenticates an image: an input with no AI marker is not proof of camera origin.

## Signed Content Credentials (optional)

Add `--sign CERT KEY` to any of the commands above to also write a signed C2PA manifest into the
output. It records a `c2pa.edited` action by snapshot-realism that declares the origin
(`trainedAlgorithmicMedia` for generated, `compositeWithTrainedAlgorithmicMedia` for edited, no
source type for camera), with the input file as its parent ingredient. When the input has Content
Credentials of its own (ChatGPT, Codex `image_gen`, Firefly, a camera), they become that
ingredient's manifest, so the chain back to the source survives. Offer it when the user wants signed
output, or when the script warns that the input's Content Credentials are not carried over.

It needs `python -m pip install c2pa-python` (0.38 or newer) on Python 3.10+. Without it, `--sign`
stops with a one-line install hint and nothing is written; ask the user to install it rather than
installing it yourself. CERT is a PEM certificate chain and KEY its unencrypted PEM private key; a
self-signed certificate is refused. If the user has no certificate,
`python <skill-dir>/scripts/make_cert.py FOLDER` writes a private root and a signing certificate
issued by it (`signer-chain.pem`, `signer-key.pem`). Tell the user that validators show that signer
as unknown or untrusted, and that the key file must stay private.

The input's manifests are copied as they are: if the script warns that they hold `stds.exif` or
similar, they can include location or names, so tell the user. The compare sheet is not signed. A
signed file's bytes differ between runs even with `--seed`; its pixels do not.

## Options

| Option | Range | Default | Effect |
|---|---|---|---|
| `INPUT` | image file | required | JPEG, PNG, WebP and other Pillow formats; HEIC if `pillow-heif` is installed |
| `--origin` | `camera`, `edited`, `generated` | required | Provenance written to the output. camera: a genuine photo (no capture effects by default). edited: a camera photo with AI-generated parts, e.g. inpainting. generated: made by an image model, including scenes built from a reference photo |
| `--preset` | `day`, `indoor`, `night` | required | The scene's actual light level; sets noise, smear, sharpening and JPEG quality |
| `--capture-strength` | 0 to 3 | 0.5 for `generated`; 0 for `camera`, `edited` and any input with camera capture metadata (with a warning) | Capture effects: 0 off, 0.5 light, 1 stronger, 2 strong, 3 maximum; independent of light level, white balance and JPEG quality |
| `-o`, `--output` | path ending `.jpg` or `.jpeg`, in an existing folder | the input name without its extension plus `_real.jpg` (`photo.png` gives `photo_real.jpg`), or `_real-2.jpg` and so on if that exists | Output file |
| `--compare` | flag | off | Also write `<output name without extension>_compare.jpg` (before on the left, after on the right); by default `photo_real_compare.jpg` for `photo.png` |
| `--overwrite` | flag | off | Replace existing output files; never the input |
| `--wb-strength` | 0 to 1 | 0.8 for `generated`; 0 for `camera`, `edited` and any input with camera capture metadata | Share of a detected color cast to remove |
| `--warmth` | -1 to 1 | 0 | Manual shift after auto white balance; negative cools, positive warms (try -0.1) |
| `--ca` | 0 to 0.01 | 0.001 | Base lateral chromatic aberration, scaled by capture strength up to 1.5x (k at most 0.015 at `--ca 0.01`); 0 disables fringing |
| `--max-edge` | 16 to 8192 | 1600 | Downscale so the long edge fits; never upscales |
| `--quality` | 1 to 100 | the preset's (85 to 90) | JPEG quality |
| `--seed` | 0 or more | random | Noise seed for repeatable output |
| `--keep-date` | flag | off | `camera` and `edited` only: copy EXIF DateTimeOriginal, OffsetTimeOriginal and SubSecTimeOriginal from the input; never GPS, Make, Model, serials or lens |
| `--sign CERT KEY` | PEM certificate chain, unencrypted PEM key | off | Also sign the output with a C2PA manifest whose parent is the input, with the input's own Content Credentials; needs c2pa-python 0.38+ (Python 3.10+). The algorithm follows the key (EC P-256, P-384, P-521, RSA, Ed25519) |
| `--version` | flag | | Print the version and exit |

Numeric options are validated before anything is read or written; out-of-range values, NaN and
infinity exit with code 2.

## Output and exit codes

The script prints the path it wrote (`wrote <path> ...`). Use that path; do not guess the name.
Other lines start with `note:`, `warning:`, `capture strength:`, `white balance:` or `signed:`. Report
the `capture strength:`, `white balance:` and `signed:` lines (when printed) and every `warning:` line
to the user.

| Exit | Meaning |
|---|---|
| 0 | Written |
| 2 | Refused: bad arguments (including a `--sign` certificate or key that cannot be read or does not match), an AI-marked input in camera mode, would overwrite, already processed, input is not a file |
| 1 | Failed: unreadable image, too small, too large, write error, `--sign` without c2pa-python, a certificate C2PA refuses. Nothing is written, unless the error lists files it could not clean up. A write error names the output path; a processing error names the input |
| 130 | Interrupted (Ctrl+C) at any stage: a one-line message, no traceback, nothing written |

If camera mode refuses, the input's metadata says it is AI-generated or AI-edited, or camera origin
cannot be confirmed (for example a C2PA manifest with no source type). Tell the user and
rerun with `--origin generated` (or `edited`) if that is true. Do not strip the metadata to get past
the check. A file that is already a snapshot-realism output is refused in every mode; use the
original.

## What the script does

Decode to sRGB (every valid embedded color profile, such as Display P3, Adobe RGB or a gray profile, is applied, transparency
is flattened onto white, 16-bit is scaled, grayscale stays grayscale, multi-frame files use frame
1), downscale in linear light, white balance, lateral chromatic aberration, signal-dependent sensor
noise (mottled luma grain plus chroma noise, strongest in shadows and fading toward the highlights,
chroma faster, none in clipped highlights, and mean-preserving so blacks are not lifted), phone
noise reduction (luma smear in flat areas, chroma blur), luma sharpening on edges only, JPEG with
4:2:0 chroma. Sharpening tapers toward the corners. There is no vignetting, because phones correct
lens shading. Toned and sepia images stay in color and get no white-balance correction. A JPEG over
50 megapixels (phone photos up to 300 MP) is reduced while decoding, so a 200 MP photo opens; a
photo too large to open safely is refused.

White balance only corrects when the scene has a clear near-neutral surface, and the
`white balance:` line says what it did and why. Foliage, cool light (blue hour, shade) and toned
monochrome get no change; strongly warm surfaces (terracotta, peach walls, skin close-ups, golden
hour) usually get little or none. Cream, beige or latte walls and pale skin can read as a warm cast,
because their color matches a gray surface under warm light: if the line says `removed` and the
result looks cold, rerun with `--wb-strength 0`.

## Metadata

Nothing from the input's metadata is kept: no EXIF, GPS, date, XMP, ICC profile or C2PA manifest. The exceptions are the opt-in `--keep-date` and `--sign`, whose manifest carries the input's C2PA manifests as its parent's.
Color outputs embed an sRGB profile; grayscale outputs stay grayscale without one. Every output gets EXIF `Software` set to `snapshot-realism 0.6.2`
and an EXIF `ImageDescription` stating the origin:

| Origin | ImageDescription | XMP DigitalSourceType |
|---|---|---|
| `camera` | Post-processed with snapshot-realism 0.6.2: configurable capture processing; origin declared as camera, not verified | none |
| `edited` | AI-edited image (camera photo with AI-generated content); post-processed with snapshot-realism 0.6.2 | `compositeWithTrainedAlgorithmicMedia` |
| `generated` | AI-generated image; post-processed with snapshot-realism 0.6.2 | `trainedAlgorithmicMedia` |

The script checks each output's metadata before writing it, and writes the image and the comparison
together or not at all. The comparison carries the same origin.

Camera mode refuses an input whose metadata says AI or cannot confirm a camera: a C2PA manifest
(ChatGPT, Codex `image_gen`, Firefly) unless it declares a camera source type and names no AI tool, an IPTC
DigitalSourceType other than a capture code, generator text (Stable Diffusion and ComfyUI chunks or
parameters), text that says the image is AI-generated, or an image generator named in a software
field. A caption that only mentions a generator (a photo of a Midjourney talk) does not count.
Finding no marker proves nothing: metadata can be stripped and invisible watermarks are not read.
The origin you pass is a declaration, not a verification.

Without `--sign`, the input's C2PA Content Credentials are not carried over (keep the untouched
original), and the origin label is an unsigned declaration in EXIF and XMP: a plain re-save or an
upload removes it, after which camera mode accepts the file, and the script replaces a signed C2PA
credential with that unsigned label. With `--sign`, the label is also signed and checked before
writing, and the input's credential is kept as the parent; a re-save or upload can still strip the
whole manifest. Camera-mode refusal prevents honest mistakes, not determined misuse, and noise,
resizing and JPEG re-encoding can weaken invisible watermarks such as SynthID.
