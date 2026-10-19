# Candid male portrait demo

One reference, one brief and one generation per path: an ordinary short request
alongside the Snapshot Realism 0.6.1 prompt recipe and script finishing. Both
request a fresh café photo, allowing changes in framing, hair, pose and expression.

[![Reference, short-request AI generation, and skill prompt plus finishing](workflow-comparison.jpg)](workflow-comparison.jpg)

## Source and rights

Reference: [Candid Picture of a Young Man Sitting and Using Phone](https://www.pexels.com/photo/candid-picture-of-a-young-man-sitting-and-using-phone-16829778/),
**UMUT RAW via Pexels**, under the [Pexels License](https://www.pexels.com/license/).
See [image rights and credits](../IMAGE-LICENSES.md) and the
[full source terms](https://www.pexels.com/terms-of-service/).

The source page lists Canon EOS RP, 85 mm, f/1.8, ISO 640, 1/60 second and a
May 14, 2023 capture date, with Lightroom processing. These are source-page
claims, not independent authentication. The downloaded reference has no camera
EXIF. It appears only inside the labelled combined comparison here; download the
standalone reference from its original source. It is not included as an MIT asset.
The photographer and subject do not endorse this project.

## What is being compared

- Reference: the unchanged stock photo, shown inside the labelled comparison.
- Ordinary AI: a [fresh first generation](standard-generation.png) from the short
  request below, using the reference only for appearance and clothing.
- Snapshot Realism: a [fresh first generation](skill-before-script.png) from the
  same brief expanded by the 0.6.1 recipe, followed by [script finishing](skill-after-script.jpg).

Both were generated on October 19, 2026, with the same reference and the same
image tool in the same session. Each panel is its path's first and only attempt.
The ordinary prompt below is the shared brief; the skill adds capture, moment,
light and background directions.

Generation is nondeterministic, so pose and scene layout differ. The script only
changes capture processing. This is one paired illustration, not a blind test
or evidence of consistent superiority. The ordinary request represents the
available image tool, not every ChatGPT UI default.

QA checked both images' phone grips, visible facial markings, window frames,
nearby chairs and table contact. For the skill result it also checked the pendant
lamp support and the foreground glass. No blocking anatomy or construction defect
was observed. Some lens-like background softness remains.

## Ordinary prompt and shared brief

```text
Use the reference for the man's appearance and clothing. Create a fresh waist-up photo of him seated at a café table, checking his phone. Vertical image.
```

## Skill-expanded prompt

```text
Candid handheld phone photo of the referenced man seated at a small table in a neighborhood café, in a fresh waist-up vertical composition. Use the reference only for his appearance and clothing: preserve his identity, existing facial markings, loosely parted dark brown hair, light stubble and off-white graphic T-shirt. Retain ordinary skin texture and a few loose hairs. He is checking his phone, looking down while his thumb scrolls, with a relaxed mouth and one forearm resting naturally on the table. Window light from the left lights the nearer cheek; the opposite cheek and the café corner are dimmer, with a ceiling lamp contributing some warm light behind him. Neutral white balance. Frame him slightly off-center from a seated friend's viewpoint, with a small handheld tilt. Keep the table edge, nearby chair and window frame coherent and readable with normal phone depth. Slight movement softness in the scrolling thumb. No captions or added graphics.
```

## Script alone

The same generated input before and after `realism.py`:

[![Skill generation before script and the same image after script](script-comparison.jpg)](script-comparison.jpg)

Left is the generated input and right is the output of `realism.py` 0.6.1; the
differences show at 100%, so open the full-size sheet.

Run from the repository root of a fresh clone, after `python -m pip install -r requirements.txt`:

```bash
python skill/snapshot-realism/scripts/realism.py assets/demo/candid-male/skill-before-script.png --origin generated --preset indoor --capture-strength 1 --wb-strength 0.8 --seed 42 --max-edge 1672 --compare -o portrait-check.jpg
```

This creates `portrait-check.jpg` and `portrait-check_compare.jpg` in the current
folder; use a new output name if those files already exist. Script version: 0.6.1.
The indoor preset matches the room, capture strength 1 is normal, and
white-balance strength 0.8 is the default for a generated image and matches the
neutral-balance request. The script left the cool window light alone. Max edge
1672 keeps the generated file's native 941 x 1672 resolution.
[`skill-after-script.jpg`](skill-after-script.jpg) and the script comparison
were made with this command and version.

Console output:

```text
white balance: no change (cool light left alone)
capture strength: 1
warning: the input's C2PA Content Credentials are not carried over (the output only states its declared origin in EXIF and XMP); keep the original, or rerun with --sign CERT KEY to carry them over as the parent of a signed manifest
```

The demo is unsigned. If you want signed Content Credentials and have a signing
certificate and private key, add `--sign CERT KEY` as documented in the main
README. It preserves the input credentials as the parent of a signed manifest.
The raw generated PNG remains available here with its original credentials.
For a lighter or rougher capture finish, rerun at strength 0.5 or 2 while retaining
the indoor preset. Inspect at native size before accepting either.

The raw PNGs retain the image tool's original provenance. The finished JPEG and
script comparison declare AI-generated origin in EXIF and XMP. See
[settings.json](settings.json) for generation counts and processing options.

The script cannot change focus geometry, facial structure, pose or absent detail.
