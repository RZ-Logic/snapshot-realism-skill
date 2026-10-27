#!/usr/bin/env python3
"""Give an image the color and capture artifacts of an ordinary phone snapshot.

Fixes the color and capture tells: the model's warm cast (when a white or gray
surface shows it), clean noiseless shadows, painted micro-texture, too-crisp
detail and lossless polish. It does
not touch face structure, pose or lighting; regenerate with a better prompt
for those.

Pipeline (linear light unless noted):
  1. decode to sRGB: orientation, embedded color profile, transparency, 16-bit
  2. downscale to phone size (never upscale)
  3. white balance, only when the scene has a clear near-neutral surface
  4. lateral chromatic aberration (red and blue fringes toward the corners)
  5. signal-dependent sensor noise: mottled luma and chroma grain, added in
     sRGB so the average level is kept
  6. phone noise reduction (sRGB): luma smear in flat areas, chroma blur
  7. edge-adaptive phone sharpening on luma (sRGB), softer toward the corners
  8. JPEG with 4:2:0 chroma subsampling, an sRGB profile and the declared origin
  9. optional (--sign): a signed C2PA manifest with the input as its parent

Usage:
  python realism.py INPUT --origin camera|edited|generated --preset day|indoor|night [--compare]
  python realism.py INPUT --origin generated --preset night --warmth -0.1 -o out.jpg
  python realism.py INPUT --origin camera --preset day --keep-date
  python realism.py INPUT --origin generated --preset day --sign signer-chain.pem signer-key.pem

Requires Python 3.9+ with numpy and Pillow. --sign also needs c2pa-python
(Python 3.10+).
"""
import argparse
import errno
import io
import json
import math
import os
import re
import stat
import struct
import sys
import tempfile
import time

if sys.version_info < (3, 9):  # the oldest version the test suite runs on
    sys.exit("snapshot-realism needs Python 3.9 or newer (this is %d.%d)" % sys.version_info[:2])

import numpy as np  # noqa: E402
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from PIL import ImageCms
    _SRGB = ImageCms.createProfile("sRGB")
    _icc = bytearray(ImageCms.ImageCmsProfile(_SRGB).tobytes())
    # A fixed, valid date so the same input gives the same bytes. Not January:
    # Pillow reads ICC months one too low, and month 0 makes it raise.
    _icc[24:36] = struct.pack(">6H", 2026, 6, 15, 0, 0, 0)
    _icc[84:100] = bytes(16)  # profile ID: all zeros means "not computed"
    SRGB_ICC = bytes(_icc)
except ImportError:  # Pillow built without LittleCMS
    ImageCms = _SRGB = SRGB_ICC = None

try:  # HEIC/HEIF (the iPhone default) when pillow-heif is installed
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

__version__ = "0.6.4"

# shot/read: noise variance = shot * signal + read (linear light).
# chroma: chroma noise relative to luma grain. smear: luma noise-reduction
# strength in flat areas. chroma_nr: chroma blur radius. sharpen: unsharp-mask
# amount (phones sharpen least at night, where it would boost noise).
# quality: JPEG quality.
PRESETS = {
    "night": dict(shot=7.5e-4, read=2.0e-6, chroma=0.55, smear=0.35, chroma_nr=3.0, sharpen=0.30, quality=85),
    "indoor": dict(shot=3.0e-4, read=8.0e-7, chroma=0.40, smear=0.20, chroma_nr=2.0, sharpen=0.40, quality=88),
    "day": dict(shot=6.0e-5, read=2.0e-7, chroma=0.25, smear=0.05, chroma_nr=1.0, sharpen=0.45, quality=90),
}
# Grain size: blur radius of the luma and chroma noise fields before their
# amplitude is set. Demosaicing and the phone's denoiser leave mottled grain a
# few pixels wide, not single-pixel speckle.
LUMA_GRAIN = 0.9
CHROMA_GRAIN = 2.0
NOISE_FLOOR = 0.5 / 255  # noise left at pure black or white (sRGB)
# Tone response of the grain, against sRGB luma y: phones denoise highlights
# hardest. Luma grain is scaled by 1 - TONE_ROLLOFF * (y - TONE_PIVOT), so
# mid-gray keeps the preset's level; chroma grain by 1 - CHROMA_FADE * y^2,
# so bright surfaces stay clean of color noise and shadows keep theirs.
TONE_ROLLOFF = 0.6
TONE_PIVOT = 0.45
CHROMA_FADE = 0.8
NOISE_CAP = 1.0  # grain sigma soft-capped at this share of the distance to black or white
# Capture strength scales noise reduction up to these caps, so the highest
# strength reads as more grain rather than as smear or color blotches.
MAX_SMEAR = 0.5
MAX_CHROMA_NR = 6.0

REC709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)  # linear luminance
REC601 = np.array([0.299, 0.587, 0.114], dtype=np.float32)  # gamma-encoded luma
BY = np.array([1.0, 0.0, -1.0], dtype=np.float32) / np.sqrt(2)  # blue-yellow (illuminant) axis
GM = np.array([-1.0, 2.0, -1.0], dtype=np.float32) / np.sqrt(6)  # green-magenta axis

# Output provenance. IPTC DigitalSourceType codes:
# https://cv.iptc.org/newscodes/digitalsourcetype/
IPTC_DST = "http://cv.iptc.org/newscodes/digitalsourcetype/"
ORIGINS = {
    "camera": None,
    "edited": ("compositeWithTrainedAlgorithmicMedia", "AI-edited image (camera photo with AI-generated content)"),
    "generated": ("trainedAlgorithmicMedia", "AI-generated image"),
}
XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
# --keep-date copies only these Exif IFD tags: DateTimeOriginal, OffsetTimeOriginal, SubSecTimeOriginal
DATE_TAGS = (0x9003, 0x9011, 0x9291)
# --sign: what the c2pa.edited action says the script did
C2PA_EDIT = ("phone-snapshot look: white balance, color fringing, sensor noise, noise reduction, sharpening, "
             "JPEG re-encode")
# c2pa-python releases before 0.38 cannot sign without a timestamp server.
C2PA_MIN = (0, 38)
# The c2pa SDK fetches a remote manifest named in an input's XMP by default:
# a network request the input's author chooses. Neither that nor OCSP runs here.
C2PA_SETTINGS = {"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False}}
# Assertions in the input's manifests that can hold personal data (EXIF with
# GPS, creator names, identity claims): --sign carries those manifests over.
C2PA_PERSONAL = re.compile(r"(stds\.exif|stds\.iptc|stds\.schema-org|c2pa\.metadata|cawg\.)", re.I)

# Large photos: a 200 MP phone or medium-format file is a photo, not an attack.
# Pillow warns above MAX_PIXELS and refuses above twice that. A JPEG above
# DRAFT_PIXELS is decoded at a reduced DCT scale (still at least twice the
# output size), so it never needs gigabytes; smaller files decode in full.
MAX_PIXELS = 300_000_000
DRAFT_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# Input checks. Metadata can be stripped, so finding nothing proves nothing.
CAMERA_SOURCE_TYPES = {"digitalcapture", "computationalcapture", "negativefilm", "positivefilm", "print",
                       "humanedits", "minorhumanedits", "algorithmicallyenhanced", "compositecapture"}
# Every IPTC code, current and retired, longest first: in C2PA CBOR the next
# key's letters can follow the code with no separator.
KNOWN_SOURCE_TYPES = sorted(CAMERA_SOURCE_TYPES | {
    "trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia", "algorithmicmedia", "compositesynthetic",
    "datadrivenmedia", "digitalart", "virtualrecording", "screencapture", "softwareimage", "digitalcreation",
    "composite"}, key=len, reverse=True)
GENERATOR_TEXT_KEYS = {"parameters", "prompt", "workflow", "invokeai_metadata", "sd-metadata", "dream",
                       "fooocus_scheme", "generation_data"}
# Explicit claims, checked in every text field, captions included: "AI-generated",
# "made with AI", "generated by Midjourney", Stable Diffusion parameters. Not a
# claim: "not AI-generated", "AI-free film", "AI Servo" focus, "AI Denoise",
# "Made in Ai, Japan", "Created in Sora, Sardinia", "Gemini Studios".
_VERB = r"(generated|created|made|rendered|edited|produced|drawn|painted)"
_TOOLS = (r"midjourney|dall[-·]?e( ?[23])?|stable diffusion|sdxl|novelai|chatgpt|gpt-4o|gpt-image|"
          r"openai|(adobe )?firefly|google imagen|imagen ?[234]|google gemini|gemini (ai|app|pro|flash|nano)|"
          r"meta ai|canva ai|bing image creator|ideogram|leonardo([. ]ai)?|flux(\.1)?|grok|comfyui|"
          r"automatic1111|(google )?magic editor|generative fill")
AI_CLAIM = re.compile(
    r"(?<!not )(?<!non-)(?<!no )\bai[- ](generated|edited|made)\b"
    r"|\b" + _VERB + r" (by|with|using|via) (an? )?ai\b(?![- ](free|servo|denoise|focus|autofocus))"
    r"|\b" + _VERB + r" (by|with|using|via|in) (" + _TOOLS + r")\b"
    r"|\bsteps: \d+, sampler: ", re.I)
# Generator names, checked only where a tool names itself (Software, CreatorTool,
# originating program, PNG text that is not a caption): a caption may be a photo
# of a Midjourney talk or an Italian "dalle". Phone and editor names stay out:
# "HDR+", "Pixel Camera", "Google Photos", Lightroom, Snapseed, Luminar, Aurora HDR.
# Imagen counts unless it follows "de" ("editor de imagen" is Spanish); FLUX needs
# its model name ("Flux" alone names unrelated apps).
AI_TOOL = re.compile(r"\b(midjourney|dall[^\sa-z0-9]{1,2}e|dalle[- ]?[23]|stable diffusion|sdxl|novelai|chatgpt|"
                     r"openai|gpt-image|gpt-4o|sora|gemini|google imagen|(?<!de )imagen|nano banana|google ai|"
                     r"flux\.1|flux[- ](pro|dev|schnell|kontext)|black forest labs|ideogram|leonardo[. ]ai|"
                     r"runway(ml)?|krea|recraft|meta ai|grok|kling|seedream|qwen[- ]image|hunyuan|firefly|"
                     r"comfyui|automatic1111|invokeai|fooocus)\b", re.I)
COMFY_EXIF = re.compile(r"^\s*(prompt|workflow)\s*:\s*[{\[]", re.I)  # ComfyUI WebP: JSON in EXIF Make/Model
CAPTION_KEYS = {"description", "comment", "title", "author", "copyright", "disclaimer", "caption"}
DST_RE = re.compile(r"digitalsourcetype/([A-Za-z]+)", re.I)
# C2PA names the tool in softwareAgent and claim_generator(_info), as a CBOR
# text string or a map with "name" and "version"; OpenAI's reads "ChatGPT" /
# "gpt-image" and "OpenAI Media Service API". Only those values are read, so a
# nearby title such as "Midjourney CEO on stage" cannot match.
C2PA_TOOL_KEYS = re.compile(rb"softwareAgent|claim_generator_info|claim_generator|claimGenerator")
CBOR_FILE_BUDGET = 20000  # CBOR items decoded per file, over every key
CBOR_MAX_STRING = 4096  # longer strings cannot be a tool name
C2PA_AI_AGENT = re.compile(r"(?i)\b(chatgpt|gpt-image|gpt-4o|openai|dall[-·]e|firefly|midjourney|"
                           r"google imagen|imagen ?[234]|gemini|nano banana|stable diffusion|stability ai|sdxl|"
                           r"ideogram|leonardo\.ai|sora|flux\.1|black forest labs|recraft|kling|seedream|"
                           r"qwen-image|hunyuan|grok|meta ai|galaxy ai|generative fill|generative expand)\b")


class _CborError(ValueError):
    pass


def _cbor_item(buf, i, depth=0, budget=None):
    """Decode the one CBOR item at buf[i]; returns (value, end).

    Bounded: nesting depth 8 and 4000 items (or the caller's shared budget).
    Any malformed or truncated input raises _CborError, never anything else.
    Floats, tags and simple values come back as None, and strings longer than
    CBOR_MAX_STRING come back empty without being copied; only short strings,
    arrays and maps matter here.
    """
    budget = budget if budget is not None else [4000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8 or i >= len(buf):
        raise _CborError()
    first = buf[i]
    major, info, i = first >> 5, first & 0x1F, i + 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        n = 1 << (info - 24)
        if i + n > len(buf):
            raise _CborError()
        arg, i = int.from_bytes(buf[i:i + n], "big"), i + n
    elif info == 31 and major in (2, 3, 4, 5):
        arg = None  # indefinite length, ends with 0xff
    else:
        raise _CborError()
    if major in (0, 1, 7):
        return None, i
    if major == 6:  # a tag: the tagged item follows
        return _cbor_item(buf, i, depth + 1, budget)
    if major in (2, 3):
        if arg is None:
            parts = []
            while i < len(buf) and buf[i] != 0xFF:
                part, i = _cbor_item(buf, i, depth + 1, budget)
                parts.append(part if isinstance(part, (bytes, str)) else b"")
            if i >= len(buf):
                raise _CborError()
            joined = b"".join(p if isinstance(p, bytes) else p.encode("utf-8") for p in parts)
            return (joined if major == 2 else joined.decode("utf-8", "replace")), i + 1
        if i + arg > len(buf):
            raise _CborError()
        if arg > CBOR_MAX_STRING:  # not a tool name; skip it without copying
            return (b"" if major == 2 else ""), i + arg
        data = buf[i:i + arg]
        return (data if major == 2 else data.decode("utf-8", "replace")), i + arg
    items = []
    count = 0
    while arg is None or count < (arg if major == 4 else 2 * arg):
        if arg is None:
            if i >= len(buf):
                raise _CborError()
            if buf[i] == 0xFF:
                i += 1
                break
        value, i = _cbor_item(buf, i, depth + 1, budget)
        items.append(value)
        count += 1
    if major == 4:
        return items, i
    if len(items) % 2:
        raise _CborError()
    return [(items[k], items[k + 1]) for k in range(0, len(items), 2)], i  # a map, as pairs


def _names_in(value, out):
    """Every text value under a "name" or "version" key, at any depth."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, tuple) and len(item) == 2:  # a map pair
                k, v = item
                if k in ("name", "version") and isinstance(v, str):
                    out.append(v)
                else:
                    _names_in(v, out)
            else:
                _names_in(item, out)


def c2pa_tool_names(raw):
    """Software agent and claim generator names (and versions) in C2PA manifests.

    Decodes the one CBOR value that follows each softwareAgent or
    claim_generator(_info) key: a plain string, or the "name" and "version"
    strings anywhere inside a map or an array of maps, in any key order.
    Each key gets at most 4000 CBOR items and the whole file CBOR_FILE_BUDGET,
    so a crafted file with thousands of keys stays fast.
    """
    names = []
    left = CBOR_FILE_BUDGET
    for key in C2PA_TOOL_KEYS.finditer(raw):
        if left <= 0:
            break
        budget = [min(4000, left)]
        start = budget[0]
        try:
            value, _ = _cbor_item(raw, key.end(), budget=budget)
        except _CborError:
            continue
        finally:
            left -= start - max(budget[0], 0)
        if isinstance(value, str):
            names.append(value)
        else:
            _names_in(value, names)
    return names


def c2pa_ai_agents(raw):
    """AI tool names a C2PA manifest gives as its software agent or claim generator."""
    found = []
    for name in c2pa_tool_names(raw):
        for m in C2PA_AI_AGENT.finditer(name):
            if m.group(1).lower() not in found:
                found.append(m.group(1).lower())
    return found


def srgb_to_linear(s):
    s = np.clip(s, 0.0, 1.0)
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055).astype(np.float32)


SRGB_LUT = srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)


def gaussian_blur(a, sigma):
    """Separable Gaussian blur over the first two axes, reflect padding.

    A radius below 0.05 px is no blur (and would overflow the kernel). Color
    images are blurred one channel at a time, which keeps the temporaries small.
    """
    if not sigma >= 0.05:
        return a
    if a.ndim == 3:
        out = np.empty(a.shape, dtype=np.float32)
        for c in range(a.shape[2]):
            out[..., c] = gaussian_blur(a[..., c], sigma)
        return out
    r = max(1, int(3 * sigma + 0.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k = (k / k.sum()).astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        p = np.pad(a, pad, mode="reflect")
        n = a.shape[axis]
        out = np.zeros(a.shape, dtype=np.float32)
        tmp = np.empty(a.shape, dtype=np.float32)
        sl = [slice(None)] * a.ndim
        for i, w in enumerate(k):
            sl[axis] = slice(i, i + n)
            np.multiply(p[tuple(sl)], w, out=tmp)
            out += tmp
        a = out
        del p, tmp
    return a


def _named_srgb(icc, profile=None):
    """Whether the profile calls itself sRGB. Only used to keep notes quiet; never to skip a conversion."""
    try:
        d = ImageCms.getProfileDescription(profile).lower() if profile is not None else icc.decode("latin-1").lower()
    except Exception:
        return False
    return "srgb" in d or "iec61966" in d


def source_types(text):
    """IPTC DigitalSourceType codes named in text or bytes, lowercased; 'unknown:<x>' for anything else."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    found = set()
    for m in DST_RE.findall(text):
        m = m.lower()
        found.add(next((k for k in KNOWN_SOURCE_TYPES if m.startswith(k)), "unknown:" + m))
    return found


def _looks_monochrome(rgb):
    """True when the image has no real color: only channel noise, or none.

    Both checks scale with the image's own channel spread (its noise), so a
    grainy black-and-white photo stays monochrome while a small logo, thin
    colored text or a fine color pattern keeps the image in color.
    """
    a = np.asarray(rgb, dtype=np.uint8)
    native = a.max(axis=2) - a.min(axis=2)
    typical = float(np.median(native[::4, ::4]))
    if typical > 24:  # most pixels are colored (a fine pattern that averaging would cancel)
        return False
    if np.count_nonzero(native > 24 + 3 * typical) > native.size * 2e-3:
        return False
    small = rgb.reduce(4) if min(rgb.size) >= 32 else rgb  # box average: noise cancels, a logo stays
    a = np.asarray(small, dtype=np.uint8)
    # A consistent signed tint (sepia, selenium, cyanotype) is color, however
    # little the channels spread: noise on gray averages to zero, a tone does not.
    a16 = a.astype(np.int16)
    if max(abs(float(np.median(a16[..., 0] - a16[..., 1]))), abs(float(np.median(a16[..., 2] - a16[..., 1])))) > 3:
        return False
    spread = a.max(axis=2) - a.min(axis=2)
    return np.count_nonzero(spread > 6 + 3 * float(np.median(spread))) <= spread.size * 1e-5


def load_srgb(img, background=(255, 255, 255)):
    """Orient and decode any Pillow image to 8-bit sRGB.

    Returns (RGB image, monochrome flag, notes). An embedded color profile is
    converted to sRGB, transparency is flattened onto white and 16-bit data is
    scaled rather than clipped.
    """
    notes = []
    try:
        frames = getattr(img, "n_frames", 1)
    except Exception:  # a broken later page or frame; frame 1 already decoded
        frames = 1
    if frames > 1:
        notes.append("using frame 1 of %d" % frames)
    icc = img.info.get("icc_profile")
    fmt = img.format
    key = img.info.get("transparency")
    img = ImageOps.exif_transpose(img)
    keyed = None  # PNG color-key transparency (tRNS): one exact color or gray level means "clear"
    if key is not None and img.mode in ("RGB", "L", "I", "I;16", "I;16L", "I;16B", "I;16N"):
        px = np.asarray(img)
        if img.mode == "RGB" and isinstance(key, tuple) and len(key) == 3:
            hit = np.all(px == np.array(key, dtype=np.int64), axis=2)
        elif img.mode != "RGB" and isinstance(key, int):
            hit = px == key
        else:
            hit = None
        if hit is not None and hit.any():
            keyed = Image.fromarray(np.where(hit, 0, 255).astype(np.uint8), "L")
        del px
    if img.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
        a = np.asarray(img, dtype=np.float64)
        if img.mode == "F":  # float data is 0 to 1, 0 to 255 or 0 to 65535
            a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
            top = a.max()
            scale = 1.0 if top <= 1.0 else 255.0 if top <= 255.0 else 65535.0
        else:  # older Pillow opens 16-bit PNG as mode I, so a dark 16-bit image must not look 8-bit
            scale = 65535.0 if img.mode.startswith("I;16") or fmt == "PNG" or a.max() > 255 else 255.0
        img = Image.fromarray(np.clip(a / scale * 255.0 + 0.5, 0, 255).astype(np.uint8))
        if scale > 255:
            notes.append("16-bit input reduced to 8 bits")
    elif img.mode == "1":
        img = img.convert("L")
    if img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif img.mode in ("PA", "RGBa"):
        img = img.convert("RGBA")
    elif img.mode == "La":
        img = img.convert("LA")
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else keyed
    base = img.convert("RGB") if img.mode == "RGBA" else img.convert("L") if img.mode == "LA" else img
    if icc and base.mode in ("RGB", "CMYK", "L"):
        # Every valid profile goes through LittleCMS: a name such as "P3 / sRGB TRC"
        # says nothing reliable about the primaries.
        want = {"RGB": "RGB", "CMYK": "CMYK", "L": "GRAY"}[base.mode]
        if ImageCms is None:  # only the standard sRGB profile, by its exact name, is taken on trust
            if not (base.mode == "RGB" and b"sRGB IEC61966-2.1" in icc):
                raise ValueError("the input has an embedded color profile and this Pillow has no ImageCms; "
                                 "convert the image to sRGB first")
        else:
            try:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                space = str(getattr(profile.profile, "xcolor_space", want)).strip().upper()
            except (OSError, ImageCms.PyCMSError):
                profile = space = None
            converted = False
            if profile is not None and space == want:
                try:
                    base = ImageCms.profileToProfile(base, profile, _SRGB, renderingIntent=1,  # relative colorimetric
                                                     outputMode="RGB")
                    converted = True
                except (OSError, ImageCms.PyCMSError):
                    pass
            if converted:
                if want == "GRAY":
                    base = base.convert("L")  # a gray profile gives R = G = B; keep the image monochrome
                if not _named_srgb(icc, profile):
                    notes.append("converted the embedded color profile to sRGB")
            elif base.mode == "CMYK":
                if profile is None:
                    raise ValueError("cannot read the embedded color profile; convert the image to sRGB first")
                notes.append("the embedded color profile is not a CMYK profile: colors are approximate")
            elif profile is not None and space != want:
                notes.append("ignored a %s color profile on %s data; treated as sRGB" % (space, want))
            else:
                notes.append("ignored an unreadable color profile; treated as sRGB")
    elif base.mode == "CMYK":
        notes.append("CMYK without a color profile: colors are approximate")
    mono = base.mode == "L"
    base = base.convert("RGB")
    if alpha is not None and alpha.getextrema()[0] < 255:
        flat = Image.new("RGB", base.size, background)
        flat.paste(base, mask=alpha)
        base = flat
        notes.append("transparency flattened onto white")
    if not mono and _looks_monochrome(base):
        mono = True
    if mono:
        notes.append("monochrome image: white balance, color fringing and chroma noise skipped")
    return base, mono, notes


def to_linear(rgb, max_edge):
    """8-bit sRGB image to linear float, downscaled in linear light so fine highlights keep their level."""
    a = np.asarray(rgb, dtype=np.uint8)
    w, h = rgb.size
    scale = max_edge / float(max(w, h))
    if scale >= 1:
        return SRGB_LUT[a]
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    out = np.empty((size[1], size[0], 3), dtype=np.float32)
    for c in range(3):
        out[..., c] = np.asarray(Image.fromarray(SRGB_LUT[a[..., c]], "F").resize(size, Image.LANCZOS))
    return np.clip(out, 0.0, 1.0, out=out)


def tone_curve_fit(y, by, gm, w, bins=16, min_weight=0.005):
    """How closely chroma follows luminance: (within-bin spread, largest step between bins).

    Pixels are binned by sRGB luma. The spread is the weighted RMS distance of
    each pixel's (blue-yellow, green-magenta) chroma from its bin's mean; the
    step is the largest change of that mean per bin between bins holding at
    least min_weight of the weight. A toned print (sepia, split tone,
    cyanotype) has chroma that is a smooth function of luminance: both are
    small. A photo under a colored light has differently colored objects at the
    same brightness (large spread), and two flat colored surfaces jump from one
    chroma to another (large step).
    """
    b = np.minimum((linear_to_srgb(y) * bins).astype(int), bins - 1)
    total = np.bincount(b, w, bins)
    mb = np.bincount(b, w * by, bins) / np.maximum(total, 1e-12)
    mg = np.bincount(b, w * gm, bins) / np.maximum(total, 1e-12)
    var = np.bincount(b, w * ((by - mb[b]) ** 2 + (gm - mg[b]) ** 2), bins)
    spread = float(np.sqrt(var.sum() / w.sum()))
    used = np.nonzero(total >= min_weight * w.sum())[0]
    step = 0.0
    for i, j in zip(used[:-1], used[1:]):
        step = max(step, float(np.hypot(mb[j] - mb[i], mg[j] - mg[i])) / (j - i))
    return spread, step


def estimate_cast(lin, radius=0.06, min_frac=0.05, min_peak=0.05, max_gm=0.10, full_by=0.40, zero_by=0.55,
                  tone_spread=0.10, tone_step=0.15, min_tint=0.05):
    """Return (log-RGB cast offset, confidence 0 to 1, reason).

    The neutral reference is the least saturated dense cluster of mid and
    bright, unclipped pixels, so a scene with no gray or white surface finds no
    cluster and gets no correction. Confidence is zero for green or magenta
    clusters (foliage, colored walls), for clusters too small to trust, for
    toned monochrome images and for cool clusters (blue hour, shade). It ramps
    down for warm clusters stronger than a typical generator cast (painted
    walls, skin, golden hour). Dark pixels are left out so a night sky or black
    clothing cannot drive the estimate. Red above zero and blue below zero
    means a warm cast. A toned image is recognized by structure, not by how
    warm it is (a strong generator cast is exactly what white balance is
    for): its chroma is nearly a function of luminance (tone_curve_fit) and it
    has a clear tint (weighted mean chroma beyond min_tint).
    """
    zero = np.zeros(3, np.float32)
    step = int(math.ceil(math.sqrt(lin.shape[0] * lin.shape[1] / 2e6)))
    if step > 1:  # a strided sample of about 2 MP finds the same cluster in far less memory
        lin = lin[::step, ::step]
    y = lin @ REC709
    ok = (y > 0.02) & (lin.max(axis=2) < 0.97)
    if ok.sum() < 64:
        return zero, 0.0, "too few usable pixels"
    c = np.log(lin[ok] + 1e-4)
    c = c - c.mean(axis=1, keepdims=True)
    by, gm = c @ BY, c @ GM
    w = np.sqrt(y[ok])
    if math.hypot(float((w * by).sum() / w.sum()), float((w * gm).sum() / w.sum())) > min_tint:
        spread, step = tone_curve_fit(y[ok], by, gm, w)
        if spread < tone_spread and step < tone_step:
            return zero, 0.0, "toned monochrome image"
    hist, be, ge = np.histogram2d(by, gm, bins=(60, 30), range=((-0.6, 0.6), (-0.3, 0.3)), weights=w)
    k = np.ones(3) / 3
    hist = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 0, hist)
    hist = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 1, hist)
    # least saturated local density peak holding at least min_peak of the
    # weight within the radius: a white shirt or wall beats a larger beige floor
    db, dg = be[1] - be[0], ge[1] - ge[0]
    bc, gc = (be[:-1] + be[1:]) / 2, (ge[:-1] + ge[1:]) / 2
    rb, rg = int(round(radius / db)), int(round(radius / dg))
    pad = np.pad(hist, ((rb, rb), (rg, rg)))
    disc = np.zeros_like(hist)
    for di in range(-rb, rb + 1):
        for dj in range(-rg, rg + 1):
            if (di * db) ** 2 + (dj * dg) ** 2 <= radius ** 2:
                disc += pad[rb + di:rb + di + hist.shape[0], rg + dj:rg + dj + hist.shape[1]]
    disc /= w.sum()
    hp = np.pad(hist, 1)
    peak = np.ones_like(hist, bool)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            peak &= hist >= hp[1 + di:1 + di + hist.shape[0], 1 + dj:1 + dj + hist.shape[1]]
    cand = (disc >= min_peak) & peak & (hist > 0)
    if not cand.any():
        return zero, 0.0, "no near-neutral surface found"
    dist = np.hypot(bc[:, None], gc[None, :])
    i, j = np.unravel_index(np.argmin(np.where(cand, dist, np.inf)), dist.shape)
    b0, g0 = float(bc[i]), float(gc[j])
    for _ in range(3):  # refine on the cluster itself
        sel = (by - b0) ** 2 + (gm - g0) ** 2 < radius * radius
        if not sel.any():
            return zero, 0.0, "no near-neutral surface found"
        b0 = float((by[sel] * w[sel]).sum() / w[sel].sum())
        g0 = float((gm[sel] * w[sel]).sum() / w[sel].sum())
    frac = float(w[sel].sum() / w.sum())
    offset = (b0 * BY + g0 * GM).astype(np.float32)
    if frac < min_frac:
        return zero, 0.0, "no dominant near-neutral surface (%.0f%% of the frame)" % (100 * frac)
    if frac > 0.98 and abs(b0) > 0.05:
        return zero, 0.0, "toned monochrome image"
    if abs(g0) > max_gm:
        return zero, 0.0, "green or magenta surface, likely colored rather than a cast"
    if b0 < 0:
        return zero, 0.0, "cool light left alone"
    conf = float(np.clip((zero_by - abs(b0)) / (zero_by - full_by), 0.0, 1.0))
    if conf == 0.0:
        return zero, 0.0, "warm surface too strong to be a cast (painted wall, skin or golden light)"
    return offset, conf, "near-neutral surface %.0f%% of the frame" % (100 * frac)


def white_balance(lin, offset, strength=0.8, warmth=0.0):
    """Remove `strength` of the cast, then shift by `warmth` (+ warmer, - cooler).

    Mean brightness is kept. A pixel pushed past white is scaled down as a
    whole instead of clipped per channel. Near-white highlights the input had
    clipped (a lamp, a window) go to neutral white at their peak instead of
    taking the correction, which would tint them cyan or blue; saturated
    bright colors (a red neon) are left to the correction.
    """
    shift = -strength * offset + np.array([0.5, 0.0, -0.5], dtype=np.float32) * warmth
    shift = shift - shift.mean()
    out = lin * np.exp(shift).astype(np.float32)
    y0, y1 = float((lin @ REC709).mean()), float((out @ REC709).mean())
    if y1 > 0:
        out *= y0 / y1
    peak = out.max(axis=2, keepdims=True)
    out = np.where(peak > 1.0, out / np.maximum(peak, 1e-6), out)
    top = lin.max(axis=2, keepdims=True)
    whiteness = np.clip((lin.min(axis=2, keepdims=True) / np.maximum(top, 1e-6) - 0.6) / 0.3, 0.0, 1.0)
    t = np.clip((top - 0.95) / 0.04, 0.0, 1.0) * whiteness
    out = out + t * (out.max(axis=2, keepdims=True) - out)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def radial_scale(ch, s):
    """Scale one channel about the image center (separable bilinear, edges clamped)."""
    h, w = ch.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    ys = np.clip(cy + (np.arange(h) - cy) / s, 0, h - 1)
    xs = np.clip(cx + (np.arange(w) - cx) / s, 0, w - 1)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    fy = (ys - y0)[:, None].astype(np.float32)
    fx = (xs - x0)[None, :].astype(np.float32)
    rows = ch[y0] * (1 - fy) + ch[y1] * fy
    return rows[:, x0] * (1 - fx) + rows[:, x1] * fx


def lateral_ca(lin, k):
    """Red slightly magnified, blue slightly shrunk: color fringes grow toward the corners."""
    if k <= 0:
        return lin
    out = lin.copy()
    out[..., 0] = radial_scale(lin[..., 0], 1 + k)
    out[..., 2] = radial_scale(lin[..., 2], 1 - k)
    return out


def flat_mask(s, edge=0.03):
    """1 in flat areas, 0 on real edges and texture (from the image before noise)."""
    gy, gx = np.gradient(s @ REC601)
    return np.exp(-(gaussian_blur(np.hypot(gx, gy).astype(np.float32), 1.0) / edge) ** 2)


def add_sensor_noise(lin, shot, read, chroma, rng):
    """Sensor noise on linear light: variance shot * signal + read, so shadows are grainiest after gamma.

    The linear-light sigma is mapped to sRGB code values through the curve's
    slope and zero-mean noise is added there, so clipping and the gamma curve
    cannot lift the average level. Grain is mottled (luma blurred about 1 px,
    chroma about 2 px, as after demosaicing) rather than single-pixel speckle.
    Grain rolls off toward the highlights and chroma grain fades there faster
    (TONE_ROLLOFF, CHROMA_FADE), as after a phone's tone-dependent denoiser.
    It tapers smoothly toward black and white, where clipping would bias the
    mean, and fades out near saturation: a clipped sensor pixel has no noise
    left. Returns linear light.
    """
    h, w, _ = lin.shape
    # Each field is made zero-mean before its amplitude is set: on a small frame
    # the blurred field has a common offset, which would otherwise shift the image.
    k = 3 if min(h, w) >= 48 else 1  # amplitudes measured on a strided sample: less memory
    n = gaussian_blur(rng.standard_normal((h, w, 3), dtype=np.float32), CHROMA_GRAIN)
    n -= n.mean(axis=2, keepdims=True)
    n -= n.mean(axis=(0, 1))
    n *= chroma / (float(n[::k, ::k].std()) + 1e-8)
    luma = gaussian_blur(rng.standard_normal((h, w), dtype=np.float32), LUMA_GRAIN)
    luma -= luma.mean()
    luma /= float(luma[::k, ::k].std()) + 1e-8
    for i in range(0, h, 128):  # row blocks: full-frame temporaries would double the peak memory
        x = lin[i:i + 128]
        s = linear_to_srgb(x)
        y = (s @ REC601)[..., None]
        slope = np.where(x <= 0.0031308, 12.92,
                         1.055 / 2.4 * np.maximum(x, 0.0031308) ** (1 / 2.4 - 1)).astype(np.float32)
        sigma = np.sqrt(shot * x + read) * slope
        t = np.clip((x.max(axis=2, keepdims=True) - 0.92) / 0.08, 0.0, 1.0)
        tone = 1.0 - TONE_ROLLOFF * (y - TONE_PIVOT)
        sigma *= (1.0 - t * t * (3.0 - 2.0 * t)) * tone
        # soft cap at NOISE_CAP of the distance to black or white: clipping then
        # moves the mean by a small share of sigma, and the grain still grows
        # with strength
        cap = NOISE_CAP * np.minimum(s, 1.0 - s) + NOISE_FLOOR
        sigma /= np.sqrt(1.0 + (sigma / cap) ** 2)
        block = n[i:i + 128]
        block *= (1.0 - CHROMA_FADE * y * y) / tone  # at most 1: chroma grain stays under the cap
        block += luma[i:i + 128, :, None]
        block *= sigma
        block += s
        n[i:i + 128] = srgb_to_linear(block)
    return n


def phone_nr(s, flat, smear, chroma_sigma):
    """Phone noise reduction on sRGB: smear luma in flat areas, blur chroma into soft blotches."""
    y = s @ REC601
    cb = s[..., 2] - y
    cr = s[..., 0] - y
    if smear > 0:
        y = y + smear * flat * (gaussian_blur(y, 1.6) - y)
    if chroma_sigma > 0:
        mix = 0.5 + 0.5 * flat
        cb = cb + mix * (gaussian_blur(cb, chroma_sigma) - cb)
        cr = cr + mix * (gaussian_blur(cr, chroma_sigma) - cr)
        del mix
    out = np.empty(s.shape, dtype=np.float32)
    out[..., 0] = y + cr
    out[..., 2] = y + cb
    del cb, cr
    out[..., 1] = (y - REC601[0] * out[..., 0] - REC601[2] * out[..., 2]) / REC601[1]
    return np.clip(out, 0.0, 1.0, out=out)


def phone_sharpen(s, amount, sigma=1.0, threshold=0.004, flat=None):
    """Unsharp mask on luma only, like a phone ISP: slight halos, faintest detail cored out.

    With flat (the pre-noise flat mask) it is edge-adaptive: real edges and
    texture are sharpened, the grain in flat areas is not. It tapers toward the
    corners, amount x (1 - 0.6 r^2) with r the distance from the center over
    the half-diagonal, where phone lenses are softer.
    """
    if amount <= 0:
        return s
    h, w = s.shape[:2]
    y = s @ REC601
    d = y - gaussian_blur(y, sigma)
    del y
    d *= np.clip((np.abs(d) - threshold) / threshold, 0.0, 1.0)
    yy = ((np.arange(h, dtype=np.float32) - (h - 1) / 2.0) / (0.5 * math.hypot(h, w))) ** 2
    xx = ((np.arange(w, dtype=np.float32) - (w - 1) / 2.0) / (0.5 * math.hypot(h, w))) ** 2
    d *= amount * (1.0 - 0.6 * (yy[:, None] + xx[None, :]))
    if flat is not None:
        d *= 1.0 - flat
    out = s + d[..., None]
    return np.clip(out, 0.0, 1.0, out=out)


def process(img, preset, wb_strength=0.8, warmth=0.0, ca=0.001, max_edge=1600, seed=None,
            capture_strength=1.0, keep_before=True):
    """Return (processed PIL image, info) for any Pillow image.

    info: cast, confidence, reason, strength (the share of the cast actually
    removed), quality (the preset's JPEG quality), notes, mono, before (the
    decoded sRGB input for a comparison sheet; None unless keep_before), and
    capture_strength.

    capture_strength is how rough the capture was, independent of white
    balance and output encoding; the supported range is zero to three. Noise
    variance grows with its square, noise reduction with it (capped), color
    fringing up to 1.5 times; sharpening never exceeds the preset's, since a
    rougher capture is not a crisper one. Zero bypasses all capture artifacts.
    """
    try:
        capture_strength = float(capture_strength)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("capture_strength must be a finite number between 0 and 3") from None
    if not math.isfinite(capture_strength) or not 0.0 <= capture_strength <= 3.0:
        raise ValueError("capture_strength must be a finite number between 0 and 3")
    p = PRESETS[preset]
    cs = capture_strength
    rgb, mono, notes = load_srgb(img)
    lin = to_linear(rgb, max_edge)
    if not keep_before:
        rgb = None
    h, w = lin.shape[:2]
    if min(h, w) < 2:
        raise ValueError("image too small: needs at least 2 px on each side after downscaling, got %dx%d" % (w, h))

    def encode(x, out=None):  # linear to sRGB in row blocks (out=x converts in place)
        out = np.empty_like(x) if out is None else out
        for i in range(0, x.shape[0], 128):
            out[i:i + 128] = linear_to_srgb(x[i:i + 128])
        return out

    cast, conf, reason = np.zeros(3, np.float32), 0.0, "monochrome image"
    if not mono:
        cast, conf, reason = estimate_cast(lin)
        if wb_strength * conf > 0 or warmth != 0:
            lin = white_balance(lin, cast, wb_strength * conf, warmth)
        if cs > 0:
            lin = lateral_ca(lin, ca * min(cs, 1.5))
    if cs > 0:
        flat = flat_mask(encode(lin))
        chroma = 0.0 if mono else p["chroma"]
        lin = add_sensor_noise(lin, p["shot"] * cs * cs, p["read"] * cs * cs, chroma, np.random.default_rng(seed))
        s = encode(lin, out=lin if lin.dtype == np.float32 else None)
        del lin
        # Capped so the highest strength still reads as more grain, not as mush.
        s = phone_nr(s, flat, min(p["smear"] * cs, MAX_SMEAR),
                     0.0 if mono else min(p["chroma_nr"] * cs, MAX_CHROMA_NR))
        s = phone_sharpen(s, p["sharpen"] * min(cs, 1.0), flat=flat)
        del flat
    else:
        s = encode(lin)
        del lin
    s *= 255.0
    s += 0.5
    out = Image.fromarray(np.clip(s, 0, 255, out=s).astype(np.uint8), "RGB")
    del s
    if mono:
        out = out.convert("L")
    info = dict(cast=cast, confidence=conf, reason=reason, strength=0.0 if mono else wb_strength * conf,
                quality=p["quality"], notes=notes, mono=mono, before=rgb,
                capture_strength=capture_strength)
    return out, info


def description(origin):
    if origin == "camera":
        return ("Post-processed with snapshot-realism %s: configurable capture processing; "
                "origin declared as camera, not verified" % __version__)
    return "%s; post-processed with snapshot-realism %s" % (ORIGINS[origin][1], __version__)


def xmp_packet(source_type):
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" '
            'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
            'Iptc4xmpExt:DigitalSourceType="%s%s"/>'
            '</rdf:RDF></x:xmpmeta>'
            '<?xpacket end="w"?>' % (IPTC_DST, source_type)).encode("utf-8")


def insert_xmp(jpeg, xmp):
    """Insert an XMP APP1 segment after SOI and the leading APP0/Exif segments.

    Byte-level, so the disclosure does not depend on the Pillow version
    (Pillow before 11 ignores the xmp= save option without an error).
    """
    if len(XMP_SIG) + len(xmp) + 2 > 65535:
        raise ValueError("XMP packet too large for one APP1 segment")
    seg = b"\xff\xe1" + struct.pack(">H", 2 + len(XMP_SIG) + len(xmp)) + XMP_SIG + xmp
    i = 2
    while jpeg[i] == 0xFF and jpeg[i + 1] in (0xE0, 0xE1):
        if jpeg[i + 1] == 0xE1 and jpeg[i + 4:i + 10] != b"Exif\x00\x00":
            break
        i += 2 + struct.unpack(">H", jpeg[i + 2:i + 4])[0]
    return jpeg[:i] + seg + jpeg[i:]


def split_app11(jpeg):
    """(the JPEG without its APP11 segments, whether one of them holds a C2PA manifest store).

    C2PA keeps its manifest store in APP11. The ingredient manifests in it (an
    OpenAI or camera manifest) name their own source types, so the EXIF and
    XMP label is checked on the rest of the file and the manifest apart.
    """
    keep, i, found = [jpeg[:2]], 2, False
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF and jpeg[i + 1] not in (0xD9, 0xDA):
        end = i + 2 + struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if jpeg[i + 1] == 0xEB:
            found = found or b"jumdc2pa" in jpeg[i:end]
        else:
            keep.append(jpeg[i:end])
        i = end
    keep.append(jpeg[i:])
    return b"".join(keep), found


def disclosure_problems(data, origin, parent_manifest=None):
    """What is missing or wrong in an encoded output's provenance metadata ([] = fine).

    A signed output (--sign) must also pass manifest_problems(). parent_manifest
    is the label of the input's own active C2PA manifest, if it had one: the
    signed output must carry it as its parent ingredient's manifest.
    """
    problems = []
    plain, signed = split_app11(data)
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        exif = im.getexif()
        try:
            sub = dict(exif.get_ifd(0x8769))
        except Exception:
            sub = {}
        try:
            gps = dict(exif.get_ifd(0x8825))
        except Exception:
            gps = {}
        icc = im.info.get("icc_profile")
        mode = im.mode
    if str(exif.get(0x010E, "")) != description(origin):
        problems.append("EXIF ImageDescription missing")
    if not str(exif.get(0x0131, "")).startswith("snapshot-realism"):
        problems.append("EXIF Software missing")
    # Only the description, the software and (with --keep-date) the capture date.
    # Older Pillow lists Exif IFD tags beside the main ones, so both places are checked.
    extra = sorted(set(exif) - {0x010E, 0x0131, 0x8769} - set(DATE_TAGS)) + sorted(set(sub) - set(DATE_TAGS))
    if extra or gps or 0x8825 in exif:
        problems.append("EXIF carries other tags (%s)" % ", ".join("0x%04X" % t for t in extra or [0x8825]))
    types = source_types(plain)
    if origin == "camera":
        if types:
            problems.append("camera output carries a DigitalSourceType")
    elif XMP_SIG not in plain or types != {ORIGINS[origin][0].lower()}:
        problems.append("XMP DigitalSourceType missing")
    if SRGB_ICC and mode == "RGB" and not icc:
        problems.append("sRGB profile missing")
    if signed:
        problems.extend(manifest_problems(data, origin, parent_manifest))
    elif parent_manifest:
        problems.append("C2PA manifest missing")
    return problems


def load_c2pa():
    """(the c2pa module, None) when signing can run here, else (None, what it needs, with the fix).

    The c2pa module is c2pa-python, the Content Authenticity Initiative's SDK.
    It is optional: only --sign and the check of a signed output use it.
    """
    if sys.version_info < (3, 10):
        return None, "c2pa-python, which needs Python 3.10 or newer (this is %d.%d)" % sys.version_info[:2]
    try:
        import c2pa
    except ImportError:
        return None, "c2pa-python: python -m pip install c2pa-python"
    except Exception as e:  # installed but cannot load here (a missing native library, say)
        why = (str(e).strip().splitlines() or [type(e).__name__])[0]
        return None, "c2pa-python, which cannot load here (%s): python -m pip install --upgrade c2pa-python" % why
    try:
        from importlib.metadata import version
        have = version("c2pa-python")
    except Exception:  # no package metadata (a source checkout): judge by the API alone
        have = None
    old = have is not None and tuple(int(n) for n in re.findall(r"\d+", have)[:2]) < C2PA_MIN
    if old or not all(hasattr(c2pa, n) for n in ("Context", "Settings", "C2paBuilderIntent")) or \
            not hasattr(c2pa.Builder, "set_intent"):
        return None, "c2pa-python %d.%d or newer%s: python -m pip install --upgrade c2pa-python" % (
            C2PA_MIN + ((" (this is %s)" % have) if have else "",))
    return c2pa, None


def c2pa_context(c2pa):
    """A c2pa Context that never goes to the network (C2PA_SETTINGS)."""
    return c2pa.Context(c2pa.Settings.from_dict(C2PA_SETTINGS))


def c2pa_store(c2pa, data, mime):
    """(the C2PA manifest store in a file's bytes as a dict, None) or (None, why it could not be read).

    A file with no manifest, or in a format C2PA does not embed in (BMP), gives
    (None, None). A manifest kept on a server is not fetched: its "why" says so.
    """
    none = tuple(getattr(c2pa.C2paError, n) for n in ("ManifestNotFound", "NotSupported")
                 if hasattr(c2pa.C2paError, n))
    try:
        with c2pa.Reader(mime, io.BytesIO(data), context=c2pa_context(c2pa)) as reader:
            return json.loads(reader.json()), None
    except none:
        return None, None
    except Exception as e:
        return None, (str(e).strip().splitlines() or [type(e).__name__])[0]


def manifest_problems(data, origin, parent_manifest=None):
    """What is missing or wrong in a signed output's C2PA manifest ([] = fine).

    The active manifest must be snapshot-realism's, validate (a signer on no
    trust list is the only failure allowed: a private certificate is fine),
    record exactly one c2pa.edited action with the origin's DigitalSourceType
    (none for camera) and no other source type, and have one parentOf
    ingredient, whose manifest is parent_manifest when that is given.
    """
    c2pa, why = load_c2pa()
    if c2pa is None:
        return ["C2PA manifest present; checking it needs " + why]
    store, why = c2pa_store(c2pa, data, "image/jpeg")
    if store is None:
        return ["C2PA manifest unreadable (%s)" % (why or "no manifest found")]
    manifests = store.get("manifests") or {}
    active = manifests.get(store.get("active_manifest")) or {}
    if not active:
        return ["C2PA active manifest missing"]
    problems = []
    if not any(g.get("name") == "snapshot-realism" for g in active.get("claim_generator_info") or []):
        problems.append("C2PA claim generator is not snapshot-realism")
    results = (store.get("validation_results") or {}).get("activeManifest") or {}
    failed = sorted({str(s.get("code")) for s in results.get("failure") or []} - {"signingCredential.untrusted"})
    if failed:
        problems.append("C2PA validation failed (%s)" % ", ".join(failed))
    elif "claimSignature.validated" not in {s.get("code") for s in results.get("success") or []}:
        problems.append("C2PA signature not validated")
    actions = [a for x in active.get("assertions") or [] if str(x.get("label", "")).startswith("c2pa.actions")
               for a in (x.get("data") or {}).get("actions") or []]
    want = IPTC_DST + ORIGINS[origin][0] if ORIGINS[origin] else None
    edits = [a for a in actions if a.get("action") == "c2pa.edited"]
    if len(edits) != 1 or edits[0].get("digitalSourceType") != want:
        problems.append("C2PA c2pa.edited action missing or not declaring the origin")
    if any(a.get("digitalSourceType") not in (None, want) for a in actions):
        problems.append("C2PA action declares another DigitalSourceType")
    parents = [i for i in active.get("ingredients") or [] if i.get("relationship") == "parentOf"]
    if len(parents) != 1:
        problems.append("C2PA parent ingredient missing")
    elif parent_manifest and (parents[0].get("active_manifest") != parent_manifest
                              or parent_manifest not in manifests):
        problems.append("the input's C2PA manifest is not carried over as the parent's")
    return problems


def signer_for(c2pa, cert, key):
    """(c2pa Signer, algorithm, signer name) for a PEM certificate chain and its unencrypted PEM key.

    The algorithm follows the key: ES256, ES384 or ES512 for EC P-256, P-384 or
    P-521, PS256 for RSA, Ed25519. The key must belong to the first
    certificate. Raises ValueError with a one-line reason. cryptography comes
    with c2pa-python.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    from cryptography.x509.oid import NameOID
    try:
        k = serialization.load_pem_private_key(key, password=None)
    except TypeError:
        raise ValueError("the private key is encrypted; export it without a passphrase") from None
    except Exception:
        raise ValueError("the key file is not a PEM private key") from None
    if isinstance(k, ec.EllipticCurvePrivateKey):
        alg = {"secp256r1": "es256", "secp384r1": "es384", "secp521r1": "es512"}.get(k.curve.name)
    elif isinstance(k, rsa.RSAPrivateKey):
        alg = "ps256"
    elif isinstance(k, ed25519.Ed25519PrivateKey):
        alg = "ed25519"
    else:
        alg = None
    if alg is None:
        raise ValueError("unsupported key type; use EC P-256, P-384 or P-521, RSA or Ed25519")
    try:
        first = x509.load_pem_x509_certificate(cert)
    except Exception:
        raise ValueError("the certificate file does not start with a PEM certificate") from None
    spki = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if first.public_key().public_bytes(*spki) != k.public_key().public_bytes(*spki):
        raise ValueError("the key does not belong to the first certificate in the certificate file")
    names = first.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    name = str(names[0].value) if names else first.subject.rfc4514_string()
    try:
        signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg, cert, key, None))
    except Exception as e:
        raise ValueError("cannot use the certificate: %s" % (str(e).strip().splitlines() or ["unknown error"])[0]) \
            from None
    return signer, alg, name


def input_mime(img):
    """The MIME type of the format Pillow read the input as (an MPO is a JPEG with more pictures)."""
    fmt = img.format or ""
    return "image/jpeg" if fmt == "MPO" else Image.MIME.get(fmt) or "application/octet-stream"


def sign_jpeg(c2pa, data, origin, signer, parent, mime, title, parent_title):
    """Embed a signed C2PA manifest in an encoded, checked output; returns (signed JPEG, parent store, why).

    The manifest says snapshot-realism edited the image: a c2pa.edited action
    with the origin's DigitalSourceType (none for camera), after the
    c2pa.opened action for the parentOf ingredient, which is the input file.
    An input with Content Credentials of its own (an OpenAI or camera
    manifest) keeps them as that ingredient's manifest, so the chain back to
    the source survives. parent is the input's bytes and mime its type; the
    parent store is the input's manifest store as a dict (None if it has none
    or it could not be read, and then why says what went wrong).
    The signed output must pass disclosure_problems, or ValueError is raised.
    """
    store, why = c2pa_store(c2pa, parent, mime)
    action = {"action": "c2pa.edited", "description": C2PA_EDIT,
              "softwareAgent": {"name": "snapshot-realism", "version": __version__}}
    if ORIGINS[origin]:
        action["digitalSourceType"] = IPTC_DST + ORIGINS[origin][0]
    manifest = {"claim_generator_info": [{"name": "snapshot-realism", "version": __version__}],
                "title": title, "format": "image/jpeg"}
    out = io.BytesIO()
    try:
        with c2pa.Builder(manifest, context=c2pa_context(c2pa)) as builder:
            builder.set_intent(c2pa.C2paBuilderIntent.EDIT)
            builder.add_action(action)
            builder.add_ingredient({"title": parent_title, "relationship": "parentOf"}, mime, io.BytesIO(parent))
            builder.sign(signer, "image/jpeg", io.BytesIO(data), out)
    except c2pa.C2paError as e:
        reason = (str(e).strip().splitlines() or [type(e).__name__])[0]
        if "certificate is invalid" in reason:
            reason += (" (a self-signed certificate is refused: sign with a certificate issued by a root or "
                       "intermediate, such as the pair make_cert.py writes)")
        raise ValueError("cannot sign: " + reason) from None
    signed = out.getvalue()
    problems = disclosure_problems(signed, origin, (store or {}).get("active_manifest"))
    if problems:
        raise ValueError("signed output failed its metadata check (%s)" % "; ".join(problems))
    return signed, store, why


def c2pa_personal(store):
    """Labels of assertions in a manifest store that can hold personal data, sorted."""
    return sorted({str(a.get("label")) for m in ((store or {}).get("manifests") or {}).values()
                   for a in m.get("assertions") or [] if C2PA_PERSONAL.match(str(a.get("label", "")))})


def encode_jpeg(img, quality, origin, dates=None):
    """Encode a phone-style JPEG whose metadata states its declared origin, and check it.

    Nothing from the input's metadata is carried over: no EXIF (GPS, camera,
    serial numbers), no XMP, no C2PA. The one exception is `dates` (Exif tag
    -> value, from capture_dates() for --keep-date), written to the Exif IFD;
    the check rejects anything there but the three capture-time tags. The
    caller must choose the origin; image appearance cannot establish provenance.
    sign_jpeg() can then add a signed C2PA manifest that carries the input's.
    """
    if origin not in ORIGINS:
        raise ValueError("origin must be one of: " + ", ".join(ORIGINS))
    exif = Image.Exif()
    exif[0x0131] = "snapshot-realism " + __version__  # Software
    exif[0x010E] = description(origin)  # ImageDescription
    if dates:
        exif[0x8769] = dict(dates)  # Exif IFD; Pillow 8.0 and later write a dict as a sub-IFD
    opts = dict(quality=quality, optimize=True, exif=exif.tobytes())
    if img.mode == "RGB":
        opts["subsampling"] = 2
        if SRGB_ICC:
            opts["icc_profile"] = SRGB_ICC
    buf = io.BytesIO()
    img.save(buf, "JPEG", **opts)
    data = buf.getvalue()
    if ORIGINS[origin]:
        data = insert_xmp(data, xmp_packet(ORIGINS[origin][0]))
    problems = disclosure_problems(data, origin)
    if problems:
        raise ValueError("output failed its metadata check (%s); nothing written" % "; ".join(problems))
    return data


def xmp_packets(raw, limit=4096, window=1 << 20, budget=8 << 20):
    """Uncompressed XMP packets in any container, found without a regex over the whole file.

    Each packet's close tag is looked for only within `window` bytes of its
    start, so a file stuffed with unterminated openings costs one pass over
    its bytes, not one per opening. An opening with no close tag in reach is
    skipped along with its window. Reading stops after `limit` packets or
    `budget` bytes of packet text, so harmless decoy packets in front cannot
    hide a later one unless there are thousands of them.
    """
    head, tail = b"<x:xmpmeta", b"</x:xmpmeta>"
    out, i, used = [], 0, 0
    while len(out) < limit and used < budget:
        start = raw.find(head, i)
        if start < 0:
            break
        end = raw.find(tail, start, start + window)
        if end < 0:
            i = start + window
            continue
        out.append(raw[start:end + len(tail)])
        used += end + len(tail) - start
        i = end + len(tail)
    return out


def ai_signals(raw, img):
    """Reasons the input's metadata says it is AI-generated or AI-edited ([] = none found).

    raw is the file's bytes and img the opened image. Finding nothing proves
    nothing: metadata is easy to strip, and invisible watermarks are not read.
    """
    reasons = []
    types = source_types(raw)
    ai_types = sorted(t for t in types if t not in CAMERA_SOURCE_TYPES)
    # C2PA manifest store: JUMBF description box 'jumd' with label 'c2pa'
    # (PNG caBX, JPEG APP11, WebP and BMFF boxes)
    if b"jumdc2pa" in raw:
        where = "PNG caBX" if raw.startswith(b"\x89PNG") else "JPEG APP11" if raw.startswith(b"\xff\xd8") else "JUMBF"
        agents = c2pa_ai_agents(raw)
        if ai_types:
            reasons.append("C2PA manifest (%s) declaring %s" % (where, ", ".join(ai_types)))
        elif agents:  # e.g. a camera ingredient (digitalCapture) edited by an AI tool that named no source type
            reasons.append("C2PA manifest (%s) naming an AI tool: %s" % (where, ", ".join(agents)))
        elif not types:
            reasons.append("C2PA manifest (%s) that declares no source type, so camera origin cannot be "
                           "confirmed" % where)
    else:
        reasons.extend("IPTC DigitalSourceType " + t for t in ai_types)

    # (where, key, text, kind): kind is "caption" (free text a person wrote),
    # "tool" (a program naming itself) or "xmp" (a packet holding both)
    texts = []
    for k, v in (getattr(img, "text", None) or {}).items():  # also loads chunks after the image data
        kl = k.lower()
        kind = "xmp" if kl == "xml:com.adobe.xmp" or kl.startswith("raw profile type") else (
            "caption" if kl in CAPTION_KEYS else "tool")
        texts.append(("text chunk '%s'" % k, k, str(v), kind))
    for k, v in img.info.items():
        if k in ("xmp", "XML:com.adobe.xmp") or k in (getattr(img, "text", None) or {}):
            continue  # XMP: the byte scan below sees it in any container and any Pillow version
        if isinstance(v, bytes) and k == "comment":
            v = v.decode("utf-8", "ignore")
        if isinstance(v, str):
            texts.append(("comment" if k == "comment" else "metadata '%s'" % k, k, v,
                          "caption" if k == "comment" else "tool"))  # a file comment is free text
    for packet in xmp_packets(raw):  # uncompressed XMP in any container
        texts.append(("XMP", "xmp", packet.decode("utf-8", "ignore"), "xmp"))
    exif = img.getexif()
    tags = dict(exif) or dict(getattr(img, "tag_v2", None) or {})  # older Pillow: TIFF tags only in tag_v2
    for tag, name, kind in ((0x010E, "ImageDescription", "caption"), (0x0131, "Software", "tool"),
                            (0x013B, "Artist", "caption"), (0x9C9C, "XPComment", "caption"),
                            (0x9C9E, "XPKeywords", "caption"), (0x010F, "Make", "make"), (0x0110, "Model", "make")):
        v = tags.get(tag)
        if isinstance(v, tuple):
            v = v[0] if v else ""
        if isinstance(v, bytes):
            v = v.decode("utf-16-le" if tag >= 0x9C9B else "latin-1", "ignore")
        if v:
            texts.append(("EXIF " + name, name, str(v), kind))
    try:
        uc = exif.get_ifd(0x8769).get(0x9286)
    except Exception:
        uc = None
    if uc:
        texts.append(("EXIF UserComment", "UserComment", _user_comment(uc), "caption"))
    try:
        from PIL import IptcImagePlugin
        iim = IptcImagePlugin.getiptcinfo(img) or {}
    except Exception:
        iim = {}
    for key in ((2, 120), (2, 25), (2, 65), (2, 105)):  # caption, keywords, originating program, headline
        vals = iim.get(key)
        for v in vals if isinstance(vals, list) else [vals] if vals else []:
            texts.append(("IPTC %d:%d" % key, "iptc", v.decode("utf-8", "ignore"),
                          "tool" if key == (2, 65) else "caption"))

    for where, key, text, kind in texts:
        if key.lower().startswith("raw profile type"):  # ImageMagick: hex-encoded profile after 3 header lines
            try:
                text = bytes.fromhex("".join(text.strip().split("\n")[2:]).replace(" ", "")).decode("utf-8", "ignore")
            except ValueError:
                pass
        if kind == "make":
            if COMFY_EXIF.match(text):
                reasons.append("ComfyUI workflow in %s" % where)
            continue
        tool_text = text if kind == "tool" else " ".join(
            a or b for a, b in re.findall(r'(?:CreatorTool|softwareAgent)(?:="([^"]*)"|>([^<]*)<)', text)) \
            if kind == "xmp" else ""
        if key.lower() in GENERATOR_TEXT_KEYS:
            reasons.append("generator %s" % where)
        elif AI_CLAIM.search(text):
            reasons.append("%s says the image is AI-generated" % where)
        elif AI_TOOL.search(tool_text):
            reasons.append("%s names an image generator" % where)
        bad = sorted(t for t in source_types(text) if t not in CAMERA_SOURCE_TYPES)
        if bad and where != "XMP":  # the byte scan above already reported uncompressed packets
            reasons.append("IPTC DigitalSourceType %s in %s" % (", ".join(bad), where))
    return list(dict.fromkeys(reasons))


CAPTURE_TAGS = (0x829A, 0x829D, 0x8827, 0x920A)  # ExposureTime, FNumber, ISO, FocalLength


def camera_capture(img):
    """'Make Model' when the input carries a camera's capture metadata, else ''.

    That means a make or model plus at least one exposure setting, in the main or the Exif IFD.
    Like ai_signals it proves nothing; it only keeps default capture effects off what looks like
    a camera photo when the declared origin says otherwise.
    """
    try:
        exif = img.getexif()
    except Exception:
        return ""
    tags = dict(exif) or dict(getattr(img, "tag_v2", None) or {})
    try:
        sub = dict(exif.get_ifd(0x8769))
    except Exception:
        sub = {}
    names = []
    for tag in (0x010F, 0x0110):  # Make, Model
        v = tags.get(tag)
        if isinstance(v, tuple):
            v = v[0] if v else ""
        if isinstance(v, bytes):
            v = v.decode("latin-1", "ignore")
        v = " ".join("".join(c if c.isprintable() else " " for c in str(v or "")).split())
        if v and not COMFY_EXIF.match(v):
            names.append(v[:32])
    if not names or not any(t in sub or t in tags for t in CAPTURE_TAGS):
        return ""
    if len(names) == 2 and names[1].lower().startswith(names[0].lower()):
        names = names[1:]  # "Canon" + "Canon EOS R5"
    return " ".join(names)


def _user_comment(v):
    """Decode EXIF UserComment by its 8-byte character code."""
    if not isinstance(v, bytes):
        return str(v)
    head, body = v[:8], v[8:]
    if head.startswith(b"UNICODE"):
        le = body[1::2].count(0) >= body[0::2].count(0)  # ASCII text in UTF-16: zeros on the high byte
        return body.decode("utf-16-le" if le else "utf-16-be", "ignore")
    return (body if len(v) >= 8 else v).decode("latin-1", "ignore")


# What --keep-date accepts: a well-formed value or nothing, so the tags cannot carry other text
DATE_FORMATS = {0x9003: re.compile(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d"),  # DateTimeOriginal
                0x9011: re.compile(r"[+-]\d\d:\d\d"),  # OffsetTimeOriginal
                0x9291: re.compile(r"\d{1,9}")}  # SubSecTimeOriginal


def capture_dates(img):
    """The input's capture time for --keep-date: {tag: text} for DATE_TAGS only ({} = none).

    Read from the Exif IFD (older Pillow lists its tags beside the main ones).
    Nothing else is read: no GPS, camera, lens or serial number.
    """
    try:
        exif = img.getexif()
    except Exception:
        return {}
    try:
        sub = dict(exif.get_ifd(0x8769))
    except Exception:
        sub = {}
    dates = {}
    for tag in DATE_TAGS:
        v = sub.get(tag, exif.get(tag))
        if isinstance(v, bytes):
            v = v.decode("ascii", "ignore")
        if isinstance(v, str):
            v = v.strip("\x00 ")
            if DATE_FORMATS[tag].fullmatch(v):
                dates[tag] = v
    return dates


def describe_cast(offset):
    gains = np.exp(offset - offset.mean())
    warm = float(offset[0] - offset[2])
    tone = "warm" if warm > 0.03 else "cool" if warm < -0.03 else "neutral"
    return "%s (R x%.2f, G x%.2f, B x%.2f)" % (tone, gains[0], gains[1], gains[2])


def side_by_side(before, after):
    """before | after; the before image is downscaled in linear light, like the output, so fine detail keeps its level."""
    before = before.convert("RGB")
    if before.size != after.size:
        a = np.asarray(before, dtype=np.uint8)
        lin = np.empty((after.height, after.width, 3), dtype=np.float32)
        for c in range(3):
            lin[..., c] = np.asarray(Image.fromarray(SRGB_LUT[a[..., c]], "F").resize(after.size, Image.LANCZOS))
        before = Image.fromarray(np.clip(linear_to_srgb(lin) * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGB")
    sheet = Image.new("RGB", (after.width * 2 + 8, after.height), (255, 255, 255))
    sheet.paste(before, (0, 0))
    sheet.paste(after.convert("RGB"), (after.width + 8, 0))
    return sheet


def compare_path(path):
    return os.path.splitext(path)[0] + "_compare.jpg"


def _replace(src, dst, tries=10):
    """os.replace that waits out a brief lock on Windows.

    Antivirus, the search indexer or OneDrive can hold a just-created file
    open for a few milliseconds, which makes a rename fail with Access denied.
    A lock that lasts (an image viewer) still fails after about half a second.
    """
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == tries - 1:
                raise
            time.sleep(0.05)


def _remove(path, tries=10):
    """os.remove that waits out a brief lock on Windows and clears a read-only flag."""
    for attempt in range(tries):
        try:
            if not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if os.name != "nt" or attempt == tries - 1:
                raise
            time.sleep(0.05)


def write_outputs(files, overwrite=False):
    """Write every (path, bytes) pair, or none of them.

    Each file goes to a temp file beside its target first. Without overwrite,
    a target that appeared while this ran is never replaced (FileExistsError).
    With overwrite, replaced files are kept as backups until every new file is
    in place, and put back if any step fails. If a lock also blocks that
    cleanup, the exception carries left_behind: one line per file left over.
    It also carries target: the output path being written when it failed.
    """
    temps, placed, ok, target = [], [], False, None
    if os.name != "nt":
        umask = os.umask(0)
        os.umask(umask)
    try:
        for path, data in files:
            target = path
            fd, tmp = tempfile.mkstemp(prefix=".snapshot-realism-", suffix=".tmp",
                                       dir=os.path.dirname(os.path.abspath(path)))
            temps.append(tmp)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if os.name != "nt":
                os.chmod(tmp, 0o666 & ~umask)  # mkstemp makes it owner-only
        for (path, _), tmp in zip(files, temps):
            target = path
            if os.path.isdir(path):
                raise IsADirectoryError(errno.EISDIR, "output path is a folder", path)
            if overwrite and os.path.exists(path):
                placed.append((path, tmp + ".bak"))  # recorded first, so an interrupt cannot lose it
                _replace(path, tmp + ".bak")
            elif overwrite:
                placed.append((path, None))
            else:  # claim the name; a file that appeared meanwhile raises FileExistsError and is never touched
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                placed.append((path, None))  # recorded before anything else can be interrupted
                os.close(fd)
            _replace(tmp, path)
        ok = True
    except BaseException as e:
        if target is not None and getattr(e, "target", None) is None:
            e.target = target
        left = []
        for path, backup in reversed(placed):
            try:
                if backup and os.path.exists(backup):
                    _replace(backup, path)
                elif not backup:
                    _remove(path)
            except OSError:
                left.append("%s (the original is at %s)" % (path, backup) if backup else
                            "%s (new or empty, could not be removed)" % path)
        for tmp in temps:
            try:
                _remove(tmp)
            except OSError:
                left.append("%s (temporary file)" % tmp)
        if left:
            e.left_behind = left
        raise
    for tmp in temps:  # success: only the backups of replaced files are left to delete
        try:
            _remove(tmp + ".bak")
        except OSError:
            pass


def bounded(kind, lo=None, hi=None):
    """argparse type: a finite number of `kind` within [lo, hi]."""
    def parse(text):
        try:
            v = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError("not a valid %s: %r" % (kind.__name__, text))
        if kind is float and not math.isfinite(v):
            raise argparse.ArgumentTypeError("must be a finite number, got %r" % text)
        if lo is not None and v < lo or hi is not None and v > hi:
            if hi is None:
                raise argparse.ArgumentTypeError("must be %s or more, got %r" % (lo, text))
            raise argparse.ArgumentTypeError("must be between %s and %s, got %r" % (lo, hi, text))
        return v
    return parse


def plan_outputs(ap, args):
    """Pick the output and comparison paths, or stop before anything is read or written."""
    if args.output:
        path = args.output
        if os.path.splitext(path)[1].lower() not in (".jpg", ".jpeg"):
            ap.error("the output is always a JPEG; name it .jpg or .jpeg: %s" % path)
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            ap.error("output folder does not exist: %s" % folder)
        targets = [path] + ([compare_path(path)] if args.compare else [])
        existing = [t for t in targets if os.path.exists(t)]
        if existing and not args.overwrite:
            ap.error("%s already exists; pass --overwrite to replace it" % existing[0])
    else:
        stem = os.path.splitext(args.input)[0] + "_real"
        path, n = stem + ".jpg", 2
        while not args.overwrite and (os.path.exists(path) or args.compare and os.path.exists(compare_path(path))):
            path = "%s-%d.jpg" % (stem, n)
            n += 1
    targets = [path] + ([compare_path(path)] if args.compare else [])
    for t in targets:
        if os.path.exists(t) and os.path.samefile(t, args.input):
            ap.error("refusing to overwrite the input file: %s" % t)
        if os.path.isdir(t):
            ap.error("output path is a folder: %s" % t)
    return path, compare_path(path) if args.compare else None


def _has_avif():
    try:
        from PIL import features
        return bool(features.check("avif"))
    except Exception:
        return False


def open_image(raw, max_edge=1600):
    """Open the file's bytes and decode them; returns the loaded Pillow image.

    A JPEG above DRAFT_PIXELS is decoded at a reduced DCT scale (1/2, 1/4 or
    1/8) that still leaves at least twice max_edge on the long side, so a
    200 MP photo needs a fraction of the memory. Metadata is read when the
    file opens, so EXIF, ICC, XMP and IPTC stay available; EXIF orientation is
    applied later, by load_srgb. Smaller files decode in full and keep the
    exact linear-light downscale. Above twice MAX_PIXELS Pillow raises
    DecompressionBombError.
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        if img.format == "JPEG" and w * h > DRAFT_PIXELS:
            f = 2.0 * max_edge / max(w, h)
            if f < 1:
                img.draft(img.mode, (int(math.ceil(w * f)), int(math.ceil(h * f))))
        img.load()  # a truncated file fails here, not halfway through the metadata checks
    return img


def _too_large(path, e):
    """The message for a DecompressionBombError, without Pillow's "DOS attack" wording."""
    m = re.search(r"\((\d+) pixels\)", str(e))
    size = " (%d megapixels)" % round(int(m.group(1)) / 1e6) if m else ""
    return "error: %s is too large%s; downscale it first" % (path, size)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # never crash on a non-ASCII file name in a cp1252 console
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    import warnings
    written = []  # set once the outputs are in place
    try:
        with warnings.catch_warnings():  # Pillow warns between MAX_PIXELS and twice that
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            return _run(argv, written)
    except KeyboardInterrupt:  # Ctrl+C while reading, checking or processing: one line, no traceback
        print("interrupted%s" % ("" if written else "; nothing written"), file=sys.stderr)
        return 130


def _run(argv, written):
    ap = argparse.ArgumentParser(description="Apply a phone-snapshot look to an image.")
    ap.add_argument("input")
    ap.add_argument("--origin", choices=tuple(ORIGINS), required=True,
                    help="provenance written to the output: camera (a genuine photo; no capture effects by default), "
                         "edited (a camera photo with AI-generated parts, e.g. inpainting), "
                         "generated (made by an image model, including scenes built from a reference photo)")
    ap.add_argument("--preset", choices=("day", "indoor", "night"), required=True,
                    help="the scene's actual light level; sets noise, smear, sharpening and JPEG quality")
    ap.add_argument("-o", "--output", help="output .jpg (default: the input name without its extension plus "
                                           "_real.jpg, so photo.png gives photo_real.jpg; then _real-2.jpg and so on "
                                           "if that exists)")
    ap.add_argument("--compare", action="store_true", help="also write <output>_compare.jpg, before | after")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files (never the input)")
    ap.add_argument("--wb-strength", type=bounded(float, 0.0, 1.0),
                    help="share of a detected color cast to remove, 0 to 1 (default 0.8 for generated; 0 for "
                         "camera and edited photos, whose own white balance already ran, and for an input "
                         "with camera capture metadata)")
    ap.add_argument("--warmth", type=bounded(float, -1.0, 1.0), default=0.0,
                    help="manual shift after auto white balance, -1 to 1: negative cools, positive warms (try -0.1)")
    ap.add_argument("--ca", type=bounded(float, 0.0, 0.01), default=0.001,
                    help="lateral chromatic aberration, 0 to 0.01 (default 0.001; 0 disables)")
    ap.add_argument("--capture-strength", type=bounded(float, 0.0, 3.0),
                    help="strength of simulated capture artifacts, 0 to 3 (default 0.5 for generated; 0 for camera "
                         "and edited photos, which already have real capture texture, and for an input with camera "
                         "capture metadata; 0 disables them); "
                         "white balance and JPEG quality are controlled separately")
    ap.add_argument("--max-edge", type=bounded(int, 16, 8192), default=1600,
                    help="downscale so the long edge fits, 16 to 8192 (default 1600; never upscales)")
    ap.add_argument("--quality", type=bounded(int, 1, 100),
                    help="JPEG quality 1 to 100 (default: the preset's, 85 to 90)")
    ap.add_argument("--seed", type=bounded(int, 0), help="noise seed (0 or more) for repeatable output")
    ap.add_argument("--keep-date", action="store_true",
                    help="keep the input's capture date and time (EXIF DateTimeOriginal, its time zone and "
                         "subseconds) for camera and edited photos; nothing else (no GPS, camera or serials)")
    ap.add_argument("--sign", nargs=2, metavar=("CERT", "KEY"),
                    help="also sign the output with a C2PA manifest whose parent ingredient is the input, with "
                         "the input's own Content Credentials (needs c2pa-python 0.38+, Python 3.10+): CERT is a PEM "
                         "certificate chain, KEY its unencrypted PEM private key (make_cert.py makes a pair)")
    ap.add_argument("--version", action="version", version="snapshot-realism " + __version__)
    args = ap.parse_args(argv)
    if args.keep_date and args.origin == "generated":
        ap.error("--keep-date is for camera and edited photos; a generated image has no capture date to keep")
    c2pa = signer = None
    if args.sign:
        c2pa, why = load_c2pa()
        if c2pa is None:
            print("error: --sign needs " + why, file=sys.stderr)
            return 1

    if not os.path.isfile(args.input):
        ap.error("input not found or not a file: %s" % args.input)
    path, cpath = plan_outputs(ap, args)
    if args.sign:
        pems = []
        for name in args.sign:
            try:
                with open(name, "rb") as f:
                    pems.append(f.read())
            except OSError as e:
                ap.error("--sign: cannot read %s: %s" % (name, e.strerror or e))
        try:
            signer, alg, signer_name = signer_for(c2pa, *pems)
        except ValueError as e:
            ap.error("--sign: %s" % e)
    try:
        with open(args.input, "rb") as f:
            raw = f.read()
        src = open_image(raw, args.max_edge)
        software = str(src.getexif().get(0x0131, ""))
        reasons = ai_signals(raw, src)
        dates = capture_dates(src) if args.keep_date else None
    except Image.DecompressionBombError as e:
        print(_too_large(args.input, e), file=sys.stderr)
        return 1
    except UnidentifiedImageError:
        ext = os.path.splitext(args.input)[1].lower()
        hint = ""
        if ext in (".heic", ".heif"):
            hint = ": install HEIC support with 'python -m pip install pillow-heif', or export a JPEG"
        elif ext == ".avif" and not _has_avif():
            hint = ": this Pillow cannot read AVIF; upgrade Pillow, or export a JPEG"
        print("error: cannot read %s as an image%s" % (args.input, hint), file=sys.stderr)
        return 1
    except Exception as e:  # Pillow raises many types for damaged files; any of them means unreadable
        print("error: cannot read %s as an image: %s" % (args.input, str(e) or type(e).__name__), file=sys.stderr)
        return 1

    if software.startswith("snapshot-realism"):
        ap.error("this file is already a snapshot-realism output (%s); run it on the original" % software)
    if args.origin == "camera" and reasons:
        ap.error("camera mode refused: the input's metadata says it is AI-generated or AI-edited, or cannot "
                 "confirm camera origin (%s). Use --origin generated, or --origin edited for an AI edit of a "
                 "camera photo" % "; ".join(reasons))
    cautions, notes = [], []
    if b"jumdc2pa" in raw and not args.sign:
        cautions.append("the input's C2PA Content Credentials are not carried over (the output only states "
                        "its declared origin in EXIF%s); keep the original, or rerun with --sign CERT KEY to "
                        "carry them over as the parent of a signed manifest"
                        % ("" if args.origin == "camera" else " and XMP"))
    if args.keep_date:
        notes.append("kept the capture date %s" % " ".join(dates[t] for t in DATE_TAGS if t in dates) if dates
                     else "the input has no capture date to keep")
    # the agent may not know a supplied image is a camera photo, so its metadata can switch the defaults off
    camera_like = "" if args.origin == "camera" else camera_capture(src)
    if camera_like:
        if args.origin == "edited":
            what = "an edited input is a camera photo, so keep --origin edited"
        elif reasons:
            what = "it may be an AI edit of a camera photo; if it is, rerun with --origin edited"
        else:
            what = ("it may be a camera photo; ask the user, then rerun with --origin camera if it is their "
                    "unedited photo or --origin edited if it was AI-edited")
        off = [n for n, v in (("capture effects", args.capture_strength), ("white balance", args.wb_strength))
               if v is None]
        cautions.append("the input carries camera capture metadata (%s)%s: %s%s" % (
            camera_like, " and an AI marker" if reasons else "", what,
            ". Default %s left off; add them only if the user asks for that look" % " and ".join(off) if off else ""))
    photo = args.origin != "generated" or bool(camera_like)  # an edited input is a camera photo too
    strength = args.wb_strength
    if strength is None:
        strength = 0.0 if photo else 0.8
    capture = args.capture_strength
    if capture is None:  # a real photo already has real capture texture; adding more only hurts it
        capture = 0.0 if photo else 0.5
        if photo and not camera_like:
            notes.append("%s photos get no capture effects by default; --capture-strength 0.5 adds them"
                         % args.origin)

    writing = False
    try:
        out, info = process(src, args.preset, strength, args.warmth, args.ca, args.max_edge, args.seed,
                            capture_strength=capture, keep_before=bool(args.compare))
        quality = info["quality"] if args.quality is None else args.quality
        data = encode_jpeg(out, quality, args.origin, dates)
        if signer is not None:
            data, store, unread = sign_jpeg(c2pa, data, args.origin, signer, raw, input_mime(src),
                                            os.path.basename(path), os.path.basename(args.input))
        files = [(path, data)]
        if cpath:
            files.append((cpath, encode_jpeg(side_by_side(info["before"], out), 90, args.origin)))
        writing = True
        write_outputs(files, args.overwrite)
        written.append(path)
    except (Exception, KeyboardInterrupt) as e:
        left = getattr(e, "left_behind", None)
        outcome = "; nothing written"
        if left:
            outcome = "; could not clean up after it, so these remain: " + "; ".join(left)
        if isinstance(e, KeyboardInterrupt):
            print("interrupted%s" % outcome, file=sys.stderr)
            return 130
        if isinstance(e, FileExistsError):
            print("error: %s appeared while this ran%s (pass --overwrite to replace it)" % (e.filename, outcome),
                  file=sys.stderr)
        elif isinstance(e, Image.DecompressionBombError):
            print(_too_large(args.input, e), file=sys.stderr)
        elif writing:  # name the file that could not be written, not the input
            reason = getattr(e, "strerror", None) or str(e) or type(e).__name__
            print("error: cannot write %s: %s%s" % (getattr(e, "target", None) or path, reason, outcome),
                  file=sys.stderr)
        else:
            print("error: %s: %s%s" % (args.input, str(e) or type(e).__name__, outcome), file=sys.stderr)
        return 1

    if signer is not None:
        if unread:  # damaged, or kept on a server (never fetched here)
            cautions.append("the input's C2PA manifest could not be read (%s), so its Content Credentials are "
                            "not carried over; keep the original" % unread)
        personal = c2pa_personal(store)
        if personal:
            cautions.append("the input's Content Credentials hold %s, which can include location, device or "
                            "names; --sign carried them into the output unchanged" % ", ".join(personal))
    for note in info["notes"] + notes:
        print("note: " + note)
    for w in cautions:
        print("warning: " + w)
    warmth = "; warmth %+.2f applied" % args.warmth if args.warmth else ""
    if not info["mono"]:
        found = "found a %s cast" % describe_cast(info["cast"]) if info["confidence"] > 0 else info["reason"]
        if info["strength"] > 0:
            print("white balance: removed %d%% of a %s cast (%s)%s"
                  % (round(info["strength"] * 100), describe_cast(info["cast"]), info["reason"], warmth))
        elif photo and args.wb_strength is None:
            print("white balance: off for %s (%s; --wb-strength 0.8 turns it on)%s"
                  % ("a likely camera photo" if camera_like else args.origin + " photos", found, warmth))
        else:
            print("white balance: no change (%s)%s" % (found, warmth))
    print("capture strength: %g" % info["capture_strength"])
    print("wrote %s (%dx%d, preset %s, origin %s)" % (path, out.width, out.height, args.preset, args.origin))
    if signer is not None:
        print("signed: C2PA manifest (%s) by %s; parent ingredient %s%s" % (
            alg.upper(), signer_name, os.path.basename(args.input),
            " with its Content Credentials" if store else " (no Content Credentials of its own)"))
    if cpath:
        print("wrote %s" % cpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
