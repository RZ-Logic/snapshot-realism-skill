# Prompt recipe

Image models drift toward a glossy, centered, studio-lit portrait because most of their training
images are edited, posed photos. A convincing phone snapshot prompt describes the *capture*: the
device, the moment, the light that was actually there and the mistakes an ordinary person makes
taking it.

## Order

Write one paragraph in this order. Each slot pushes away from a specific tell.

1. **Capture**: device and conditions. "Candid iPhone photo, handheld", "phone photo at night,
   handheld" or "cheap Android phone, on-camera flash". For night, describe what a phone night shot
   looks like (noisy, smeared shadows, blown lights, one side brighter) in the light and artifacts
   slots, and do not pair "iPhone" with "high ISO, grainy": that is a camera-settings phrase, not
   what a phone produces. Do not mention night mode at all, not even negated: phones denoise heavily
   in it, which gives the clean look this skill removes, and a negated term tends to pull the
   concept in. Never a lens spec or "DSLR, 85mm": that gives the studio portrait look.
2. **Subject**: a specific, ordinary person. Default to a fictional person: an exact age, build and
   one or two distinguishing features. Use a supplied real person only when it is the user or
   someone who consented (confirm that first); then preserve their identity and describe only
   visible, relevant details. Real skin: visible pores, uneven tone, a blemish or two, under-eye
   shadows. Everyday makeup or none. Hair with flyaways, frizz, a strand stuck to the cheek; never
   "glossy" or "flowing". Generators polish women hardest (smooth symmetric face, styled hair, a
   soft camera smile), so for a woman push each of these unless the request sets it (an occasion,
   outfit, makeup or time of day): no makeup or a little worn-off everyday makeup, hair as it sits
   at that time of day (tied back, flattened or frizzy), an expression caught mid-thought rather
   than a smile for the camera, and ordinary clothes she would wear that day.
3. **Moment**: mid-action and not posing. Talking, mid-laugh, glancing off-frame, half-turned,
   holding a drink or phone. Avoid eye contact plus a closed-mouth smirk: that is the default AI
   pose.
4. **Light**: name the real sources and their colors. "Lit by a white festival floodlight from the
   left and blue stage wash from behind, face partly in shadow". At night: one side brighter,
   highlights blown, shadows muddy. With flash: hard shadow behind the subject, shiny forehead.
5. **Framing**: off-center, slight tilt, head or shoulder cut by the frame edge, a stranger's arm or
   blurred shoulder in the foreground, horizon not level. Frame tighter when a wide view of a room
   would show more staged background than the request needs, keeping the background readable
   rather than blurred.
6. **Artifacts**: one or two capture imperfections the scene would really produce, such as "noisy
   shadows" at night or "slight motion blur" for a quick snap. A full checklist of flaws (noise,
   blur, missed focus, compression, all at once) becomes its own uniform tell. Ask for neutral or
   cool white balance; generators lean warm. If the user wants warm light (candles, a lamp,
   sunset), name that source in the light slot and leave the neutral request out; then run the
   script with `--wb-strength 0`.

## Scene and background

Generated rooms drift toward a showroom: every cushion plumped, objects lined up, nothing in use.
Before generating, name the place specifically and as it is used, taking the place and time from the
request ("a rented one-bedroom living room", not "a modern living room"). If the setting supports
it, add one or two cues drawn from the request or from what the person is doing, such as slightly
settled seat cushions on a sofa used every day; the examples on this page are patterns, not phrases
to reuse. Let the named light fall off across the room (a far corner darker, a window brighter than
the wall): even light everywhere reads as a listing photo. Do not add clutter, stains or wear by
default: a new sofa, a tidy home, a hotel or a showroom can be pristine. Keep the user's subject,
outfit and locked scene details.

Image models put house plants in almost every room. Do not mention plants unless the request or
the place calls for them (a garden centre, a plant shop): a negated "no plants" tends to pull them
in. If corners or sills are in frame, name what that place would really have there. Plants count
toward the showroom tell only when they look staged (several, evenly placed, a styled corner plant)
or the user asked to remove them.

After generating, check the two or three most prominent background objects and where the subject
touches them. Judge visible evidence, not tidiness:

- **Construction and contact:** proportions, perspective and support (shelves, furniture legs,
  window frames), and contact shadows where objects meet a surface. Clearly impossible construction
  or support, including an object that floats with no contact, is Gate A; occlusion or an unfamiliar
  design is not.
- **Arrangement:** objects lined up, evenly spaced or squared to the camera, or house plants
  placed like a styled set, in a place the user wants lived in. A tidy room is not a defect.
- **Materials:** creases and cushion compression that match weight and use; no repeated identical
  objects or textures, no uniformly inflated upholstery. Grain cannot add these.
- **Light and depth:** background light, reflections and softness agree with the scene. Do not hide
  a broken background with blur.

Name the problem and revise that object or arrangement in the next prompt. Arrangement and material
signs together are one Gate B tell, the showroom row, however many objects show them. A background
fix the user asked for is enough to regenerate on its own, within the 3-regeneration cap. When
handing back, say which background objects you checked.

## Words to drop and words to translate

Some words pull the image back toward the polished look. They fall into two tiers, and the agent
tells the user what it dropped and what it translated.

### Drop

Pure polish tokens with no scene content. Remove them even if the user wrote them, and say so: 8k, 4k, ultra detailed, highly detailed, masterpiece, hyperrealistic, photorealistic, realistic, award-winning, stunning, gorgeous, flawless, perfect, smooth skin, glowing skin, cinematic, professional photo.

"Photorealistic" and "realistic" are on the list on purpose: they describe a render, not a photo.

### Translate by intent

These words can carry a real requirement, so deleting them loses information. Translate these by intent (do not just delete): soft light, sharp focus, model, influencer, bokeh, shallow depth of field, studio lighting, golden hour, dramatic lighting, rim light, beautiful, attractive, editorial, portrait photography, modern, minimalist, luxury, aesthetic, cozy.

| Word | What to write instead, and when to keep it |
|---|---|
| soft light | Name the actual source: "overcast window light", "a shaded porch". |
| sharp focus | Say what is in focus: "the phone screen in focus, the background soft". |
| model, influencer | Keep when it is the person's job ("a fashion model backstage"). Otherwise describe the person. |
| bokeh, shallow depth of field | Phone portrait mode only if the user wants a blurred background; otherwise ask for a normal phone photo with the background readable. |
| studio lighting | Keep when the scene is a studio. Otherwise name the real source. |
| golden hour | Keep the time of day ("low evening sun from the left"); drop the glow. |
| dramatic lighting, rim light | Name the source and its direction; if the user wants high contrast, say what is bright and what is dark. |
| beautiful, attractive | Describe the specific features the user means (freckles, a strong jaw, gray streaks in the hair). |
| editorial, portrait photography | Keep if the user wants that genre, and tell them it works against a snapshot look. |
| modern, minimalist, luxury, aesthetic, cozy | Keep the style the user wants, but name a specific real place ("a minimalist studio apartment someone lives in", "a hotel suite after housekeeping"); on their own these words produce a showroom or a styled set. Pristine is fine where the place really is pristine. |

Say in one line what was dropped and what was translated, for example: "Dropped: 8k, masterpiece.
Translated: soft light to overcast window light; beautiful to freckles and a crooked smile."

## Example

User asks for: "a woman at a music festival at night with headphones, make it look real"

Rewritten prompt:

> Candid phone photo taken by a friend at a crowded outdoor music festival at night, iPhone,
> handheld. A fictional woman, about 27, long dark brown hair with frizz
> and flyaways, a few strands stuck to her cheek, visible pores, slightly uneven skin tone, makeup
> mostly worn off, small hoop earring. Black leather jacket over a black t-shirt, over-ear DJ
> headphones around her neck. She is mid-laugh and looking to the right of the camera, not posing.
> Lit by a harsh white floodlight from the left and a blue-purple stage wash from behind; the right
> side of her face falls into shadow. Framed off-center to the left, slightly tilted, the top of
> her head cut off by the frame, a blurred stranger's shoulder in the lower right corner. Noisy,
> smeared shadows, blown-out lights, neutral white balance.

## Quality gates

Check the generated image before running the script. Two gates.

### Gate A: blocking

Any one of these means regenerate, or crop it out if cropping leaves a usable image:

- broken anatomy: extra, fused or missing fingers or limbs, a warped face, impossible joints
- readable text that is garbled or reversed (signs, shirt prints, labels)
- identity drift from a supplied reference
- physically impossible light, reflections, object construction or support
- the image does not show what was asked

Prompt fixes: keep hands out of frame or holding an object; ask for signs out of focus or out of
frame; for identity drift, reduce what you ask to change and regenerate.

### Gate B: stylistic tells, counted

Two or more means regenerate with a new prompt (not an edit request) that pushes harder on the
slot that failed.

| Tell | Fix in the prompt | Fixable by the script? |
|---|---|---|
| Centered pose with eye contact and a closed-mouth smirk | Moment + framing slots | No |
| Symmetric, poreless or airbrushed face | Subject slot, drop or translate beauty words | Partly (added grain); the face shape stays |
| Glossy hair clumps with no flyaways | Subject slot | No |
| Even, flattering light on a night scene | Light slot | No |
| Creamy uniform background blur | Drop or translate the blur words (see above), ask for a phone | No |
| Showroom background in a place the user wants lived in: objects lined up or evenly spaced, identical repeats, uniformly plump upholstery, staged house plants | Scene and background pass: a specific place in use, one or two supported cues, light that falls off, tighter framing | No |

The script fixes clean noiseless shadows and over-crisp lossless-looking detail, so they do not
count toward Gate B. A warm yellow-orange cast counts as fixed only when the script's
`white balance:` line says `removed`: it corrects only when a white or gray surface shows the cast.
If the line says `no change` and the cast is unwanted, rerun with `--warmth -0.1`, or count it as a
Gate B tell and regenerate with "neutral white balance" in the artifacts slot.

### Cap

After 3 regenerations, one of two things happens:

- A Gate A defect remains: hand the image back as a "failed draft" that names the defects. Do not run
  the script on it and do not present it as finished unless the user asks.
- Only Gate B tells remain: hand back the best attempt and name them.

### After the script

Inspect the processed image and the comparison at fit size and at 100%. For 100%, crop
native-resolution regions (face, hands, any text, a shadow area, a prominent background object)
from both images and view them side by side. If grain, smear or sharpening looks artificial, lower
`--capture-strength` or keep the unprocessed image. Keep the preset matched to the scene's actual
light level. Capture strength is independent: 0.5 light (default), 1 stronger, 2 strong, 3
maximum. It changes capture artifacts, not face, hair, pose or lighting structure. When handing
back, offer a rerun stronger (`1`) if it looks too clean, or at `0` if the grain looks wrong.

## Iterating

Avoid vague edit requests such as "more realistic", "less AI" or "add grain". For a new scene,
rewrite the failed prompt slots and generate fresh, then post-process. For reference-guided
generation, name the details the user wants retained: for example, the same person, café and outfit.
Allow small natural posture, expression and hair changes unless the user locks them. Target
requested skin and hair rendering changes explicitly. Preserving identity or setting does not
require freezing the pose or surface texture. Compare against the reference for identity drift;
generation cannot guarantee an exact match. Check the result against both quality gates.

Label a reference-guided variation as a generative change, rather than a script-only before/after.
Compare that variation's raw image with its own processed output to isolate capture processing.
Capture strength cannot repair structural tells left in the generated image.
