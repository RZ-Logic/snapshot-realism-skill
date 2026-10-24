# Changelog

## 0.6.2 (2026-10-24)

From the maintainer's 22-case review (20 generated scenes against plain generations, plus 2 of his own phone photos): no script defect in any case except too much grain at strength 1 at night, strength 0.5 picked as best in 10 of 17 generated cases, and the script making his own photos worse or no different.

### Script

- `--capture-strength` defaults to 0.5 for `generated` and `edited` (was 1). Strength 1 and above are unchanged.
- Camera photos get no capture effects by default (`--origin camera` defaults to strength 0, with a note): a real photo already has real phone texture. `--capture-strength` still adds them on request.
- The agent may not know that a supplied image is a camera photo, so the script checks too: an input with camera capture metadata (a make or model plus exposure settings) and no AI marker gets no capture effects or white balance by default, whatever origin was declared, and a warning that it may be a camera photo. Explicit `--capture-strength` and `--wb-strength` still apply. A photo whose metadata was stripped (messaging apps, screenshots) is not detected, so the instructions also ask.

### Skill instructions

- For an image the user supplies, the agent asks whether it is a photo they took instead of assuming it is generated.

- The camera-photo path is now "fix camera imperfections": ask which imperfection to fix and leave a photo with nothing to fix alone; make a conservative AI edit for flare, streaks, ghosting, haze or dust (the script cannot remove fringing, noise or blur); check signs and text at 100%; then label the result without changing its color, size or texture (`--capture-strength 0 --wb-strength 0 --max-edge 8192 --quality 95`), as `generated` when the image model returned a whole new frame, as Codex and ChatGPT do, and `edited` only for a masked inpaint. Without an image tool, the agent gives the user the edit prompt.
- Prompt recipe: generators polish women hardest, so unless the request sets them, the subject slot pushes everyday makeup or none, hair as it sits at that time of day, a mid-thought expression and ordinary clothes; the recipe's example follows it. House plants and flowers are kept out unless the request asks for them or the place sells them: the prompt names what sits on the surfaces near the subject and ends with "No house plants or flowers.", and any unasked plant counts toward the showroom tell. In a blind 54-image Codex test (2026-10-24), plants appeared in 0 of 27 images with this rule and 22 of 27 without it. Face cues tested alongside it (plain structural features, bare-face wording, light described by its dark side) showed no measurable gain and are not included.
- Hand-back offers a rerun stronger (1) or at 0, from the new 0.5 default; the recipe's strength scale matches.
- The skill description, the Codex default prompt, the `--origin` help and the Windows launcher no longer describe camera photos as processed directly.

### Docs

- The Muskoka Wharf script example pins `--capture-strength 1`, the setting its outputs were made with, and says camera photos get no capture effects by default.
- Quickstart: the camera example is the fix-imperfections flow, and the default output names refer to the generated example.
- The two Muskoka Wharf AI edits (`plaza-after.jpg`, `sculpture-after.jpg`) carry the AI-generated label in their metadata again. The supplied copies had lost it in transfer; it was re-added with `realism.py --origin generated --capture-strength 0 --wb-strength 0`, which applies no capture effects.
- The Wharf edit captions say how the edits were made: Codex's image model removed the streaks and regenerated each scene, `realism.py` finished it and Codex exported Ultra HDR. The script has no flare-removal step.
- Limits: the generation and editing stage can fix faces, hands, text and objects when asked but can also redraw signs, text and fixtures, so check the result at 100%.

### Tests and CI

- The test that an output named as a case variant of the input (`photo.jpg` for `Photo.JPG`) is refused never ran on macOS. It was gated on `os.path.normcase`, which folds case only on Windows, while macOS's default APFS volume is also case-insensitive. The test now probes its own temp folder and skips only where `photo.jpg` does not find `Photo.JPG`.
- The workflow moves to `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node 24. The v4 and v5 it used targeted Node 20, which GitHub has deprecated; every job ran them forced onto Node 24 with a warning.

### Releases

- New `release.yml` workflow, run by hand from the Actions tab on main. It runs the tests and the ZIP check, then publishes the version in `realism.py` as a GitHub release: tag `vX.Y.Z` on that commit, the version's CHANGELOG section as the notes and `snapshot-realism.zip` attached. It refuses a version that is already tagged. `tools/release_notes.py` extracts the notes; three tests cover it (a mutation pass killed 3 of 3 mutants) and check that the workflow uses only actions the test workflow runs.
- The README install steps link to the ZIP of the latest release.

## 0.6.1 (2026-10-19)

Instructions only. Pixels, metadata checks and signing are unchanged from 0.6.0; only the version string in the output metadata moves.

### Backgrounds

- Generated rooms drift toward a showroom: every cushion plumped, objects lined up, nothing in use. The recipe gains a scene and background pass. Before generating, the agent names the place specifically and as it is used, taking the place and time from the request ("a rented one-bedroom living room", not "a modern living room"); adds at most one or two cues drawn from the request or what the person is doing; lets the named light fall off across the room (a darker far corner, a window brighter than the wall), because even light everywhere reads as a listing photo; and frames tighter when a wide room would show more staged background than the request needs, keeping the background readable rather than blurred. It does not add clutter, stains or wear by default: a new sofa, a tidy home or a hotel can be pristine. The recipe's examples are marked as patterns, not phrases to reuse, so the fix does not become its own uniform tell.
- After generating, the check covers the two or three most prominent background objects and where the subject touches them, judging visible evidence (construction and contact, arrangement, materials, light and depth) rather than tidiness, and the hand-back says which objects were checked. The 100% inspection includes a background crop.
- Gate A also covers clearly impossible object construction or support, such as a shelf that floats with no contact or fused furniture legs. Gate B gains a showroom-background tell (objects lined up or evenly spaced, identical repeats, uniformly plump upholstery, in a place the user wants lived in). All of its signs count as one tell however many objects show them, and a background fix the user asked for is enough to regenerate on its own, within the 3-regeneration cap.
- Modern, minimalist, luxury, aesthetic and cozy join the translate-by-intent list: keep the style the user wants, but name a specific real place; pristine is fine where the place really is pristine.

### Hand-back

- The agent offers to rerun the script a step lighter or stronger than the strength it used (`0.5` or `2` from the default `1`): lighter if the grain looks too strong, stronger if it looks too clean. There is no 0.75 step: flat-gray luma noise at strengths 0.5, 0.75 and 1 measures 0.57, 0.79 and 1.02 DN (day), 1.10, 1.56 and 2.00 (indoor) and 1.62, 2.27 and 2.83 (night), too close together to tell apart; the CLI still accepts any value from 0 to 3.

### Docs

- The README Quality gates and the ChatGPT instructions carry the same changes. The ChatGPT text was tightened to stay under its 7,900-character limit (7,883).

## 0.6.0 (2026-10-19)

The 2026-10-18 review's main provenance gap was that the script replaced an input's signed C2PA credential (OpenAI's, for a ChatGPT image) with an unsigned EXIF and XMP label that one re-save removes. This release adds opt-in signing that keeps the input's credential as the parent of a new, signed manifest. Pixels are unchanged from 0.5.0, and so is the default path.

### Signing

- New opt-in `--sign CERT KEY`. The output JPEG gets a signed C2PA manifest: claim generator `snapshot-realism 0.6.0`, a `c2pa.opened` action for the parent ingredient, and one `c2pa.edited` action whose `digitalSourceType` follows `--origin` (`trainedAlgorithmicMedia` for `generated`, `compositeWithTrainedAlgorithmicMedia` for `edited`, none for `camera`). The parent ingredient (`parentOf`) is the input file; when the input has Content Credentials of its own, its manifest store comes along as the ingredient's manifest. Measured on the ChatGPT demo PNG: the output holds snapshot-realism's manifest and OpenAI's ("OpenAI Media Service API", `c2pa.created` by ChatGPT `gpt-image`), and the SDK reports a single failure code, `signingCredential.untrusted`, for the private test signer.
- It needs c2pa-python 0.38 or newer, which needs Python 3.10 or newer. Without it, `--sign` exits 1 with a one-line install hint before reading the input, and nothing is written. On Python 3.9 the line says signing needs Python 3.10 (the newest c2pa-python that installs on 3.9, 0.9.0, does not import there). Releases 0.32 to 0.37 cannot sign without a timestamp server, so the script asks for an upgrade. The default path still needs only NumPy and Pillow.
- New `scripts/make_cert.py FOLDER [--name] [--days]` writes `signer-chain.pem` (a signing certificate, then the private root that issued it) and `signer-key.pem` (EC P-256, mode 0600 on POSIX). The C2PA SDK refuses a certificate that signs itself, which is why it makes a root. Validators show such a signer as untrusted or unknown; the signature still proves the file is unchanged since signing. There is no timestamp, so the certificate's dates bound the signature (five years by default).
- The algorithm follows the key: ES256, ES384 or ES512 for EC P-256, P-384 or P-521, PS256 for RSA, Ed25519. An unreadable, encrypted or mismatched key or certificate is an argument error (exit 2) before any processing; a certificate the SDK refuses at signing (a self-signed one) exits 1 with a hint that names `make_cert.py`.
- The signed output is read back before it is written. `disclosure_problems` now checks the EXIF and XMP label on the file without its APP11 segments (the parent's manifest names its own source types) and, when a manifest is present, `manifest_problems` requires snapshot-realism's claim generator, a validated signature with `signingCredential.untrusted` as the only allowed failure, exactly one `c2pa.edited` action that declares the origin, no other source type in the actions, one parent ingredient, and the input's own active manifest as that parent's. Without c2pa-python, a file with a manifest is reported as not checkable rather than passed.
- The SDK fetches a remote manifest named in an input's XMP by default. The script turns that and OCSP fetching off, so a crafted input cannot make it send a network request (a test serves the URL on localhost and counts zero requests). A manifest it cannot read, remote or damaged, gets a warning that it was not carried over.
- The input's manifests are copied unchanged. When they hold assertions that can carry personal data (`stds.exif`, `stds.iptc`, `stds.schema-org`, `c2pa.metadata`, CAWG), the script prints a warning that names them.
- Without `--sign`, the warning for an input with Content Credentials now names `--sign`. The compare sheet is never signed. With `--seed` the pixels repeat but signed bytes do not.

### Tests and CI

- `tests/test_c2pa_signing.py`: 32 tests. The 7 that cover running without c2pa-python run everywhere (6 on the 3.9 floor, where one needs Python 3.10); the 25 signing tests skip without c2pa-python 0.38. A mutation pass over the new code killed 18 of 18 mutants.
- The CI matrix gains an Ubuntu Python 3.13 job with c2pa-python, so the signing tests run in CI.

## 0.5.0 (2026-10-18)

A seven-agent review scored 0.4.0 at 6 out of 10: high craft in orientation, ICC handling and write safety, but a capture texture that read as white "Add Noise" grain amplified by sharpening. This release reworks the texture, closes the provenance and large-file gaps the review found, and states the provenance limits plainly.

### Capture texture

- Noise is mottled instead of white. The luma grain is blurred by about 1 px and the chroma grain by about 2 px before their amplitude is set, as demosaicing and a phone denoiser leave it. On a flat patch at 60 the horizontal lag-2 autocorrelation measures 0.25 to 0.48 across presets and strengths (0.4.0: 0.06 or less); the tests require at least 0.15.
- Grain follows a phone denoiser's tone response. Luma grain is scaled by 1 minus 0.6 (y minus 0.45), where y is sRGB luma, so mid-gray keeps the preset's level and highlights get less: at strength 1 the grain at 200 is 0.40 to 0.41 of the grain at 20 in every preset (night: 5.06, 3.68, 2.83 and 2.07 DN at 20, 60, 118 and 200; the tests require 0.3 to 0.5). Chroma grain fades faster, by 1 minus 0.8 y squared, so a bright shirt or wall carries fine neutral grain instead of color blotches: chroma noise on gray 200 measures 0.4 to 1.6 DN across presets at strengths 1 and 3, at most 0.65 of its level on gray 60 (the tests allow 2.0 DN and 0.7).
- Sharpening is edge-adaptive. It is weighted by the pre-noise flat mask, so flat areas no longer have their grain boosted: flat-patch grain measures the same with sharpening on and off (0.4.0 raised it by up to 1.9x at day strength 3; the tests allow 1.05x). A step edge still shows 12 to 19 DN of overshoot at strength 1 (the tests require 5).
- Sharpening tapers toward the frame corners (amount times 1 minus 0.6 r squared). There is still no vignetting, because phones correct lens shading.
- Noise is mean-preserving. It is added in sRGB code values, its sigma is soft-capped at the distance to black or white, and it fades in clipped highlights, so a flat patch keeps its mean within 1.5 DN (2.5 DN near black) at strengths 1 and 3, and an all-black image stays black. Noise clipping no longer lifts blacks: a black patch moves by at most 0.15 DN at any strength and a patch at 20 by under 1 DN (0.46 DN at night strength 3 in the README table; at that setting 0.4.0 lifted black by 11.6 DN). The cap also means grain below about sRGB 6 barely grows with strength (night, gray 6: 3.50, 3.63 and 3.76 DN at strengths 1, 2 and 3).
- Capture strength now means how rough the capture was. Noise variance still scales with strength squared. Noise reduction grows with strength up to a cap, so the top strengths read as more grain rather than as smear or color blotches: luma smear stays capped at 0.5, as in 0.4.0, and the chroma blur is now capped at 6.0 (uncapped before, so 9.0 at night strength 3). At strength 3 the grain's lag-4 autocorrelation at 60 is 0.11 indoors and at night (the tests allow 0.2). Sharpening never exceeds the preset (preset times the smaller of strength and 1). Color fringing stops growing at 1.5x: its scale k is `--ca` times the smaller of strength and 1.5, so at most 0.015 at `--ca 0.01` (0.4.0 grew k with strength and capped it at 0.01). Strength 0 still bypasses fringing, noise, noise reduction and sharpening exactly.
- Toned and sepia images stay in color and keep their tone. A consistent signed tint (median R-G or B-G beyond about 3 DN) is no longer treated as monochrome, and white balance recognizes a toned image by its structure rather than by how warm it is: its chroma is nearly a function of luminance. Binned into 16 sRGB-luma bins, a toned image has a within-bin chroma spread under 0.10, a largest step between bins under 0.15 and a mean tint beyond 0.05; it then gets the reason "toned monochrome image" and no correction. A split tone keeps its R-B of 32 (0.4.0 pulled it to 11) and an additive sepia its R-B of 21 (0.4.0 wrote it as grayscale). A strong generator cast on a real scene is still corrected: differently colored objects at one brightness give a spread of 0.26 or more in the test scenes and demo photos (toned images: about 0.01), and two flat surfaces of different color give a step of 0.36 or more (0.78 for skin against a warm-cast gray wall). Neutral gray texture is still written as a grayscale JPEG.
- The compare sheet downscales the before image in linear light, like the processed image, so the two halves have the same brightness.
- PNG color-key transparency (tRNS on RGB, gray and 16-bit gray) is flattened onto white with a note.
- No RuntimeWarning for tiny strengths.
- Memory: float32 noise, white-balance estimation on a subsample of about 2 MP, and the "before" image is only kept when `--compare` is set. Peak memory on a 2048 by 1536 image measures 56 bytes per output pixel (the test allows 80; 0.4.0 measured 97).
- The claim that strength 1 keeps the 0.3.0 pixels is gone: the noise domain, grain size and sharpening changed, so output differs from 0.3.0 and 0.4.0 at every strength. Read noise is lower in every preset (night 1.7e-5 to 2e-6, indoor 5e-6 to 8e-7, day 1e-6 to 2e-7), because added in sRGB the old night value alone gave 13.6 DN of grain at black. Shot noise, chroma noise, smear, chroma noise reduction, sharpening and JPEG quality (90, 88, 85) are unchanged.

### Provenance and input

- The XMP scan is bounded. Each packet's close tag is searched only within 1 MB of its start, and reading stops after 4,096 packets or 8 MB of packet text; an opening with no close tag in reach is skipped with its window, so a real packet after a decoy is still read. In 0.4.0 a crafted file took 12.7 s at 8,000 unterminated packet openers and grew with the square of the count; 0.5.0 reads 16,000 in 0.011 s. A JPEG whose only marker is an APP1 XMP `DigitalSourceType` is still refused in camera mode.
- The CBOR decode budget for C2PA manifests is per file, not per key: a 128 KB manifest with 2,000 `claim_generator` keys is read in under 0.5 seconds.
- Generator names in software fields now also cover current tools (ChatGPT, OpenAI, gpt-image, GPT-4o, DALL-E, Sora, Gemini, Imagen, Nano Banana, Google AI, Midjourney, Stable Diffusion, SDXL, FLUX.1, Black Forest Labs, Ideogram, Leonardo.Ai, Runway, Krea, Recraft, Meta AI, Grok, Kling, Seedream, Qwen-Image, Hunyuan, and Firefly in the software field). "Adobe Lightroom", "Snapseed", "GIMP", "HDR+", "Pixel Camera" and "Google Photos" are still accepted, "Imagen" after "de" (Spanish "editor de imagen") does not count, and a caption that only mentions a generator still does not count.
- Photos up to 300 megapixels are accepted without a warning, and Pillow refuses only above 600 MP (0.4.0 refused a 200 MP phone photo as a decompression bomb). A JPEG over 50 MP is reduced while decoding, so memory stays small; metadata and orientation still apply. Measured: a 200 MP JPEG runs in 1.5 s with a 293 MB peak working set, and a 108 MP JPEG in 2.1 s with 552 MB (0.4.0: 5.8 s and 2,015 MB). A photo too large to open safely ends with "is too large (N megapixels); downscale it first" and exit 1, with no Pillow warning text.
- Ctrl+C at any stage (read, scan, process, write) exits 130 with a one-line "interrupted" message and no traceback. Exit code 130 is listed in every exit-code table.
- Errors from writing name the output: "cannot write <output path>: <reason>". Errors from processing still name the input.
- New opt-in `--keep-date` for `camera` and `edited`. It copies only EXIF DateTimeOriginal, OffsetTimeOriginal and SubSecTimeOriginal from the input, never GPS, Make, Model, serials or lens data. With `generated` it is an argument error (exit 2). Without the flag the output has no capture time.

### Launcher and blind-test kit

- `realism.bat` resolves the Python interpreter once (falling back to `py -3`), so a pyenv-win `python.bat` shim can no longer end the launcher silently. In drop mode, every relative piece after a full path is skipped as a fragment, whatever happened to the full path.
- `tools/blind_test.py` builds numbered side-by-side sheets in random left and right order with a key, and scores answers with a one-sided binomial p-value. No blind test has been run yet.
- `tools/measure.py` measures the capture texture on synthetic patches and the demo photos, with fixed seeds, and generates the tables in the README's "Does it work?" section (`--readme`). `--script` measures another copy of `realism.py`, such as an older release.

### Docs

- Prompt recipe: the single banned list is now two tiers. "Drop" holds pure polish tokens; "Translate by intent" holds words that can carry a real requirement (soft light, sharp focus, model, bokeh, studio lighting, golden hour, beautiful, editorial and others) with what to write instead and when to keep them. The agent tells the user what it dropped and translated.
- After 3 regenerations, a remaining Gate A defect is handed back as a "failed draft" that names the defects, without running the script on it. If only Gate B tells remain, the best attempt is handed back with the tells named.
- Night prompts no longer pair "iPhone" with "high ISO, grainy"; they describe what a phone night shot looks like. The artifacts slot asks for one or two imperfections, not a checklist, which becomes its own uniform tell.
- README Provenance has a threat model. The label is an unsigned declaration in EXIF and XMP, a Pillow re-save at quality 80 removes it (after which camera mode accepts the file), the script replaces a signed C2PA credential with the unsigned label, and noise, resizing and JPEG re-encoding can weaken invisible watermarks such as SynthID. Camera-mode refusal prevents honest mistakes, not determined misuse.
- README has a Related work table, installation steps for Claude.ai, Claude Code, Codex, Google Antigravity and ChatGPT Projects, a Quality gates section and a Reference section. SKILL.md has compatibility frontmatter and the Claude.ai upload workflow. The ChatGPT Project instructions are under 7,900 characters.
- The legacy salon fixtures left the repository. The Muskoka Wharf AI-assisted photo edits sit beside a separate camera-photo script example (`--origin camera --preset night --seed 42`). The candid-male output, its before and after sheet and the workflow sheet were regenerated with 0.5.0 from the command in its README, and a test now runs that command and compares both files.

### Tests

- The suite has 236 tests (114 in 0.4.0) and runs in about 25 seconds. `test_v05_pipeline.py` and `test_v05_io.py` pin every property above; `test_v05_guards.py` guards 0.4.0 behaviour the review found unguarded (one camera-mode refusal per detector, the metadata self-check, each white-balance gate, auto-naming, the strength-0 bypass, sharpening coring and edge-aware chroma noise reduction); `test_v05_mutant_guards.py` pins behaviour that mutation testing showed unguarded (the `--keep-date` format and IFD0 checks, the Ctrl+C rollback, the CBOR string cap, write errors without a file name, the XMP close-tag window and packet limit, the noise means and highlight fade, and the direction of color fringing); `test_blind_test.py` and new launcher cases cover the kit and the launcher fixes.
- Mutation testing on the final 0.5.0 script, with the full suite run per mutant: 107 mutants, made up of the 23 that survived the 0.4.0 review (adapted to the new code), 28 more of the review's mutants as a regression check, and 56 written against 0.5.0 code. 100 are killed and 1 is caught only by the README measure-table check (chroma noise not made zero-sum per pixel). 6 survive. Two are equivalent: a gray ICC profile already gives R = G = B, and the `--keep-date` self-check's GPS clause repeats the IFD0 check, which already flags the GPS pointer. Two change only what a crafted C2PA manifest costs to read: the per-key and per-file CBOR budgets each bound the other. One is now redundant: the older near-uniform-tint rule for toned images, which the structural rule catches first on every test image. One is a gap: the early exit after the ComfyUI check on EXIF Make and Model, which only matters if a camera's Make or Model text reads like an AI claim; no test has one. In 0.4.0, 22 of the review's 52 mutants survived.
- Tests that used the salon fixtures now use the candid-male generation, which carries a real OpenAI C2PA manifest. The whites-and-skin test runs on that one image, because the plain-prompt generation has no white surface. The demo test reads the command from the candid-male README and checks both shipped files, and the README measure tables are compared with a fresh run of `tools/measure.py`.

### Packaging

- `agents/openai.yaml` leads with the origin label in its default prompt.
- The packaging test checks that the drop list and the translate list are identical in the prompt recipe and the ChatGPT instructions.

## 0.4.0 (2026-10-15)

- Added `--capture-strength 0..3` (default 1), independent of the scene's light preset, white balance, manual warmth, resize, JPEG quality and declared origin.
- Strength 0 bypasses color fringing, sensor noise, phone noise reduction and sharpening. Strength 1 retains v0.3.0's pixel processing; version metadata changes, so output JPEGs are not byte-identical across releases.
- Strength scales noise amplitude linearly (shot/read variance by strength squared), color fringing up to 0.01, luma smear up to 0.5, chroma noise-reduction radius and sharpening. Try 0.5 for light, 2 for strong or 3 for maximum, then inspect the result at 100%.
- The Windows launcher now offers off (0), light (0.5), normal (1) and strong (2) capture strength for each image.
- Instructions separate capture intensity from lighting presets and from skin, hair, pose or scene changes that require generation or a targeted generative edit.

## 0.3.0 (2026-10-13)

### Safety

- `--origin` is required and now takes `camera`, `edited` or `generated`; `edited` is a camera photo with AI-generated parts such as inpainting.
- Camera mode refuses input whose metadata says AI (exit 2): C2PA manifests without a camera source type or whose software agent or claim generator names an AI tool (even when an ingredient declares a camera capture), non-capture IPTC `DigitalSourceType` codes (also inside compressed PNG text and ImageMagick raw profiles), generator text (Automatic1111 and ComfyUI chunks, Stable Diffusion parameters in EXIF UserComment), text that says the image is AI-generated, and image generators named in software fields (EXIF Software, XMP CreatorTool, IPTC originating program, PNG tool text). C2PA tool names are decoded from the structured `softwareAgent` and `claim_generator` values only, in any key order, any CBOR length form, and across arrays of several generators. Captions (EXIF descriptions, file comments, PNG descriptions) that only mention a generator, and ordinary words such as "imagen", "firefly" or the Italian "dalle", do not trigger it. A C2PA manifest with no source type is refused with its own message: camera origin cannot be confirmed. Finding no marker proves nothing.
- Nothing from the input's metadata is kept: no EXIF, GPS, date, XMP, ICC or C2PA. The script warns when it drops C2PA Content Credentials.
- Every output gets EXIF `Software` and `ImageDescription` stating its origin; `edited` and `generated` also get an IPTC XMP `DigitalSourceType`, written at byte level so it works on any Pillow version.
- Every output is checked for its metadata before it is written.
- Input that is already a snapshot-realism output is refused in every mode.
- Scope: for fictional people, your own likeness, or people who gave permission; never to hide AI origin. An AI edit of a real person's likeness is never declared `camera`.

### Color

- White balance only corrects when the scene has a clear near-neutral surface, and the script prints what it did and why. Foliage, cool light and toned monochrome get no change; strongly warm surfaces usually get little or none. Cream, beige or latte walls and pale skin can still read as a cast.
- `--wb-strength` defaults to 0.8 for `edited` and `generated` and 0 for `camera`.
- Near-white pixels no longer shift blue (Lab b* shift from -4.1 to -5.7 down to 0.0 to +0.7 on the demo images) and skin keeps 95% to 99% of its chroma (was 75% to 86%).
- Bright whites under a cast are corrected all the way up. Near-white highlights the input had clipped (a lamp, a window) go to neutral white instead of turning cyan or blue; saturated bright colors such as a red neon keep their color.
- Every valid embedded color profile (Display P3, Adobe RGB, CMYK, grayscale) goes through LittleCMS to sRGB; a name that says "sRGB" no longer skips the conversion. An unreadable profile, or one for the wrong color space, on an RGB or gray image is ignored with a note that says which; on a CMYK image a wrong-space profile falls back to the approximate conversion. Transparency is flattened onto white; 16-bit and float input is scaled; grayscale stays grayscale (grainy black-and-white photos too), and an image with a small colored area or a fine color pattern stays in color. Color outputs embed an sRGB profile with a fixed, valid creation date.

### Capture

- `--preset` is required (`day`, `indoor`, `night`): the scene's actual light level.
- New order: decode, downscale in linear light, white balance, lateral CA, sensor noise, phone noise reduction, luma sharpening with coring, JPEG.
- Sensor noise fades to zero in clipped highlights; night shadow noise is lower (added std 10.6 to 7.6) and blacks stay above crushed (1st percentile 1 to 4).
- Sharpening is strongest in `day` (0.45) and weakest at `night` (0.30). Preset JPEG quality is 90, 88 and 85 for day, indoor and night.

### CLI

- Numeric options are validated before anything is read or written: `--wb-strength` 0 to 1, `--warmth` -1 to 1, `--ca` 0 to 0.01, `--max-edge` 16 to 8192, `--quality` 1 to 100, `--seed` 0 or more; NaN and infinity are rejected.
- Output naming: the input name without its extension plus `_real.jpg`, then `_real-2.jpg`, `_real-3.jpg`; the script prints the path it wrote. `-o` must be a `.jpg` or `.jpeg` in an existing folder, and an existing `-o` target needs `--overwrite`.
- The input is never overwritten, even with `--overwrite`; the comparison path and case variants are checked too.
- The output and the comparison are written together or not at all, after both encode and pass the metadata check; if a lock also blocks the cleanup, or Ctrl+C interrupts it, the message lists what was left instead of saying nothing was written; a file that appears while the script runs is never replaced without `--overwrite`. On Linux and macOS outputs get normal permissions.
- Exit codes: 0 written, 2 refused, 1 failed. Output lines start with `note:`, `warning:`, `white balance:` or `wrote `, and a non-ASCII file name no longer crashes the console.
- On Windows a rename that antivirus, the search indexer or OneDrive briefly blocks is retried for up to half a second.
- Unreadable, truncated or unsupported files end with a one-line error instead of a traceback. The same input, seed and library versions give byte-identical output. HEIC works when `pillow-heif` is installed (a one-line hint otherwise); multi-frame files use frame 1 with a note.

### Launcher

- `realism.bat` asks for each file's origin (camera, edited, generated) and light level.
- Names with `!`, `^`, `%`, `&`, parentheses, commas, semicolons and non-ASCII characters reach the script intact when quoted; a dropped name with an unquoted `&` can no longer run a command; on a drop, the relative pieces of a name Windows split are skipped, never matched to an unrelated file, while plain relative names and quoted names typed at a prompt (even after a missing one) are processed as given.
- No answer or Ctrl+C at a prompt skips the file instead of choosing camera. Refused and failed runs are counted, the summary line lists written, refused, failed and skipped, and the exit code is 1 when anything failed or was refused.
- It checks for Python 3.9+, NumPy and Pillow once before the first prompt.

### Docs

- README rewritten for the three origins, with a Scope note, a Does it work? section, an options table, the provenance table and a Claude Code install path.
- Two quality gates for generated images: Gate A (blocking defects) and Gate B (counted stylistic tells), with a cap of 3 regenerations and an inspection at 100% after the script.
- The prompt recipe no longer mentions night mode in its examples; a warm cast counts as fixed only when the script says it removed one.
- The demo notes that the middle image still has garbled shirt-sleeve text, a defect the script cannot fix.

### Packaging

- `requirements.txt` added; Python 3.9+, NumPy 1.19.3+, Pillow 8.0.0+ (verified with the full suite).
- `python tools/build_zip.py` builds a byte-identical ZIP for the same source and adds the LICENSE; `--check` verifies the ZIP matches the source.
- Tests for the script, the Windows launcher and packaging; GitHub Actions runs them on Windows, macOS and Linux plus the oldest supported Python, NumPy and Pillow.

## 0.2.0 (date not recorded)

- `--origin camera|generated` became required and a camera-photo path was added.

## 0.1.0 (2026-10-10)

- First version.
