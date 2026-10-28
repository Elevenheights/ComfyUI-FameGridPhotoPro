"""Native RawTherapee adapter. No processing implementation is copied from it."""
import functools
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from contextlib import contextmanager

import numpy as np
from PIL import Image, ImageCms
import tifffile
from . import color_tools

VERSION = "5.13"
MAX_PIXELS = 16_777_216
CONTROLS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks", "temperature", "tint", "vibrance", "saturation", "texture", "clarity", "dehaze")


@contextmanager
def work_directory():
    directory = tempfile.TemporaryDirectory(prefix="famegrid-photo-job-")
    try:
        (Path(directory.name)/"lease.json").write_text(json.dumps(dict(kind="famegrid-photo-job-v1", pid=os.getpid(), created=time.time())), encoding="utf-8")
        yield directory.name
    finally:
        for attempt in range(6):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 5: raise
                time.sleep(.05)


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]; kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]; kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return ctypes.get_last_error() != 87  # Protect access-denied/unknown processes.
        try:
            code=ctypes.c_ulong(); return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True


def cleanup_abandoned_jobs():
    root = Path(tempfile.gettempdir()).resolve()
    for path in root.glob("famegrid-photo-job-*"):
        try:
            marker=path/"lease.json"
            if path.is_symlink() or not path.is_dir() or path.resolve().parent != root or not marker.is_file(): continue
            stat = marker.stat()
            if stat.st_size > 512 or time.time()-stat.st_mtime < 300: continue
            lease=json.loads(marker.read_text(encoding="utf-8"))
            if lease.get("kind") != "famegrid-photo-job-v1" or not isinstance(lease.get("pid"), int) or lease["pid"] <= 0 or time.time()-lease.get("created",time.time()) < 300 or process_alive(lease["pid"]): continue
            shutil.rmtree(path)
        except (OSError, ValueError, TypeError): pass


def number(value, low, high, label, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"Invalid {label} ({low}–{high}).")
    return value


def settings(raw=None):
    if isinstance(raw, str):
        if len(raw) > 32_768:
            raise ValueError("Photo settings are too large.")
        raw = json.loads(raw or "{}")
    if not isinstance(raw, (dict, type(None))):
        raise ValueError("Invalid photo settings.")
    raw = raw or {}
    a = raw.get("adjustments", {})
    if not isinstance(a, dict):
        raise ValueError("Invalid adjustments.")
    a = {k: number(a.get(k, 0), -4 if k == "exposure" else -100, 4 if k == "exposure" else 100, k) for k in CONTROLS}
    crop = raw.get("crop", dict(x=0, y=0, width=1, height=1))
    if not isinstance(crop, dict):
        raise ValueError("Invalid crop.")
    crop = {k: number(crop.get(k), 0, 1, f"crop {k}") for k in ("x", "y", "width", "height")}
    if not crop["width"] or not crop["height"] or crop["x"] + crop["width"] > 1.000001 or crop["y"] + crop["height"] > 1.000001:
        raise ValueError("Crop must stay inside the image.")
    rotation = number(raw.get("rotation", 0), 0, 270, "rotation", True)
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Invalid rotation.")
    resize = raw.get("resize")
    if resize is not None:
        if not isinstance(resize, dict):
            raise ValueError("Invalid resize dimensions.")
        resize = {k: number(resize.get(k), 1, 16384, k, True) for k in ("width", "height")}
        if resize["width"] * resize["height"] > MAX_PIXELS:
            raise ValueError("Exports are limited to 16 megapixels.")
    sharp = dict(enabled=False, method="usm", amount=40, radius=.75, threshold=20, haloControl=True, iterations=20, damping=20)
    grain = dict(enabled=False, strength=25, iso=400, gamma=1)
    for name, target in (("sharpening", sharp), ("grain", grain)):
        if not isinstance(raw.get(name, {}), dict):
            raise ValueError(f"Invalid {name}.")
        target.update({k: v for k, v in raw.get(name, {}).items() if k in target})
        if not isinstance(target["enabled"], bool):
            raise ValueError(f"Invalid {name} toggle.")
    if sharp["method"] not in ("usm", "rld") or not isinstance(sharp["haloControl"], bool):
        raise ValueError("Invalid sharpening method or halo control.")
    for key, low, high in (("amount", 0, 100), ("radius", .2, 3), ("threshold", 0, 100), ("iterations", 5, 50), ("damping", 0, 100)):
        number(sharp[key], low, high, key, key == "iterations")
    for key, low, high in (("strength", 0, 100), ("iso", 20, 6400), ("gamma", .2, 3)):
        number(grain[key], low, high, key, key != "gamma")
    fmt = raw.get("format", "png")
    depth = raw.get("bitDepth", 8)
    if fmt not in ("png", "jpeg", "webp", "tiff") or depth not in (8, 16, 32) or isinstance(depth, bool) or (depth == 32 and fmt != "tiff") or (fmt in ("jpeg", "webp") and depth != 8):
        raise ValueError("Choose 8-bit PNG/JPEG/WebP/TIFF, 16-bit PNG/TIFF, or 32-bit float TIFF.")
    quality = number(raw.get("quality", 95), 50, 100, "quality", True)
    mixer, skin = color_tools.validate(raw, number)
    return dict(adjustments=a, crop=crop, rotation=rotation, resize=resize, sharpening=sharp, grain=grain, colorMixer=mixer, skinTone=skin, format=fmt, bitDepth=depth, quality=quality)


def jsround(value):
    return math.floor(value + .5)


def profile(raw=None):
    s = settings(raw); a = s["adjustments"]; sh = s["sharpening"]; g = s["grain"]
    def group(name, values):
        def text(v):
            if isinstance(v, bool): return str(v).lower()
            if isinstance(v, (float, int)): return f"{jsround(v * 100000) / 100000:g}"
            return v
        return f"[{name}]\n" + "\n".join(f"{k}={text(v)}" for k, v in values.items()) + "\n"
    multipliers = [a["texture"] * x for x in (.004, .008, .005)] + [a["clarity"] * x for x in (.005, .008, .005)]
    return "\n".join([
        group("Version", dict(AppVersion=VERSION, Version=353)),
        group("Exposure", dict(Auto=False, Compensation=a["exposure"], Brightness=0, Contrast=a["contrast"], Saturation=a["saturation"], Black=jsround(-a["blacks"] * 32.768), HighlightCompr=0, ShadowCompr=0, Curve="0;", Curve2="0;", HistogramMatching=False)),
        group("HLRecovery", dict(Enabled=False)),
        group("ToneEqualizer", dict(Enabled=any(a[k] != 0 for k in ("shadows", "highlights", "whites")), Band0=0, Band1=jsround(a["shadows"] * .35), Band2=jsround(a["shadows"] * .65), Band3=jsround(a["highlights"] * .5), Band4=jsround(a["whites"] * .65 + a["highlights"] * .2), Band5=jsround(a["whites"] * .65), Regularization=2, Pivot=0)),
        group("White Balance", dict(Enabled=a["temperature"] != 0 or a["tint"] != 0, Setting="Custom", Temperature=jsround(6504 * 2 ** (a["temperature"] / 200)), Green=math.exp(-a["tint"] * .0025), Equal=1, TemperatureBias=0)),
        group("Vibrance", dict(Enabled=a["vibrance"] != 0, Pastels=a["vibrance"], Saturated=0, ProtectSkins=True, AvoidColorShift=True, PastSatTog=False)),
        group("Directional Pyramid Equalizer", dict(Enabled=a["texture"] != 0 or a["clarity"] != 0, Gamutlab=True, cbdlMethod="bef", **{f"Mult{i}": max(.15, 1 + v) for i, v in enumerate(multipliers)}, Threshold=.2, Skinprotect=0)),
        group("Dehaze", dict(Enabled=a["dehaze"] != 0, Strength=a["dehaze"], Saturation=50, Depth=25, ShowDepthMap=False)),
        group("Sharpening", dict(Enabled=sh["enabled"] and sh["amount"] > 0, Method=sh["method"], Contrast=sh["threshold"], Radius=sh["radius"], Amount=jsround(sh["amount"] * 3), OnlyEdges=False, HalocontrolEnabled=sh["haloControl"], HalocontrolAmount=80, DeconvRadius=sh["radius"], DeconvAmount=sh["amount"], DeconvDamping=sh["damping"], DeconvIterations=sh["iterations"])),
        group("Locallab", dict(Enabled=g["enabled"] and g["strength"] > 0, Selspot=0, Name_0="Film grain", Isvisible_0=True, SpotMethod_0="full", Shape_0="RECT", Loc_0="3000;3000;3000;3000;", CenterX_0=0, CenterY_0=0, Transit_0=100, Expblur_0=True, Complexblur_0=2, BlMethod_0="blur", BlurMethod_0="norm", Radius_0=1.5, Strength_0=0, Sensibn_0=100, Isogr_0=g["iso"], Strengr_0=g["strength"] if g["enabled"] else 0, Scalegr_0=100, Divgr_0=g["gamma"], Usemaskb_0=False)),
        group("Color Management", dict(InputProfile="(embedded)", WorkingProfile="ProPhoto", OutputProfile="No ICM: sRGB output", OutputProfileIntent="Relative", OutputBPC=True, ApplyLookTable=False)),
        *[group(k, dict(Enabled=False)) for k in ("Resize", "Crop", "Directional Pyramid Denoising", "PostResizeSharpening")],
    ])


@functools.lru_cache(maxsize=2)
def color_profile(linear=False):
    data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    if linear:
        for i in range(struct.unpack_from(">I", data, 128)[0]):
            entry = 132 + i * 12
            if data[entry:entry + 4] not in (b"rTRC", b"gTRC", b"bTRC"): continue
            offset, length = struct.unpack_from(">II", data, entry + 4)
            if length < 16: raise ValueError("Unsupported ICC tone curve.")
            data[offset:offset + length] = bytes(length)
            struct.pack_into(">4sIHHI", data, offset, b"para", 0, 0, 0, 65536)
            struct.pack_into(">I", data, entry + 8, 16)
        data[84:100] = bytes(16)
    return bytes(data)


def to_linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((np.maximum(rgb, 0) + .055) / 1.055) ** 2.4).astype(np.float32)


def to_srgb(rgb):
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - .055).astype(np.float32)


def resize_float(pixels, width, height):
    if pixels.shape[:2] == (height, width): return pixels.copy()
    return np.stack([np.asarray(Image.fromarray(pixels[..., c]).resize((width, height), Image.Resampling.LANCZOS)) for c in range(pixels.shape[-1])], -1).astype(np.float32)


@functools.lru_cache(maxsize=8)
def find_engine(configured=None):
    candidates = [configured, os.environ.get("FAMEGRID_RAWTHERAPEE_PATH"), os.environ.get("CREATOR_STUDIO_RAWTHERAPEE_PATH"), shutil.which("rawtherapee-cli")]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    candidates += list((local / "CreatorStudio/engines/rawtherapee/5.13").glob("*/rawtherapee-cli.exe"))
    candidates += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "RawTherapee/5.13/rawtherapee-cli.exe", Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file(): continue
        try:
            p = subprocess.run([str(candidate), "-q", "-h"], capture_output=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if re.search(rb"RawTherapee,?\s+version\s+5\.13(?:\D|$)", p.stdout + p.stderr, re.I): return str(candidate)
        except (OSError, subprocess.TimeoutExpired): pass
    raise RuntimeError("The native photo engine is unavailable. Check the Photo Pro installation and configured engine path.")


def run_native(directory, input_path, raw, cancel=lambda: None):
    folder = Path(directory); output = folder / "developed.tif"; pp3 = folder / "settings.pp3"
    pp3.write_text(profile(raw), encoding="utf-8")
    env = dict(os.environ, RT_SETTINGS=str(folder / "preferences"))
    with (folder / "engine.log").open("w+b") as log:
        p = subprocess.Popen([find_engine(), "-q", "-o", str(output), "-p", str(pp3), "-t", "-b32", "-Y", "-c", str(input_path)], stdout=subprocess.DEVNULL, stderr=log, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 90
        try:
            while p.poll() is None:
                cancel()
                if time.monotonic() > deadline: raise TimeoutError("Photo processing timed out.")
                try: p.wait(timeout=.15)
                except subprocess.TimeoutExpired: pass
            if p.returncode or not output.is_file():
                raise RuntimeError(f"Photo processing failed (exit {p.returncode}).")
            with tifffile.TiffFile(output) as tif:
                if tif.pages[0].dtype != np.dtype("float32"): raise RuntimeError("The engine did not return float32 pixels.")
                return tif.asarray().astype(np.float32)
        finally:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=3)
                except subprocess.TimeoutExpired: p.kill(); p.wait()


def prepare(rgb, raw=None, alpha=None, preview=False, detail=False):
    s = settings(raw)
    rgb = np.asarray(rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or rgb.shape[0] * rgb.shape[1] > 60_000_000 or not np.isfinite(rgb).all(): raise ValueError("Invalid RGB image.")
    alpha = np.ones((*rgb.shape[:2], 1), np.float32) if alpha is None else np.asarray(alpha, dtype=np.float32).reshape(*rgb.shape[:2], 1)
    alpha = np.clip(alpha, 0, 1)
    rgba = np.concatenate((to_linear(rgb) * alpha, alpha), -1)
    rgba = np.rot90(rgba, -(s["rotation"] // 90))
    h, w = rgba.shape[:2]; c = s["crop"]
    x, y = min(w - 1, jsround(c["x"] * w)), min(h - 1, jsround(c["y"] * h))
    cw, ch = max(1, min(w - x, jsround(c["width"] * w))), max(1, min(h - y, jsround(c["height"] * h)))
    rgba = rgba[y:y + ch, x:x + cw]
    w, h = (s["resize"][k] for k in ("width", "height")) if s["resize"] else (cw, ch)
    if w * h > MAX_PIXELS and (not preview or detail): raise ValueError("Reduce output dimensions to 16 megapixels.")
    if preview and not detail:
        factor = min(1, 1200 / max(w, h), math.sqrt(1_000_000 / (w * h))); w, h = max(1, int(w * factor)), max(1, int(h * factor))
    rgba = resize_float(rgba, w, h)
    # Resample premultiplied linear RGB so hidden colors in transparent pixels
    # cannot bleed into visible edges. Native processing receives straight RGB.
    straight = np.divide(rgba[..., :3], rgba[..., 3:4], out=np.zeros_like(rgba[..., :3]), where=rgba[..., 3:4] > 1e-7)
    return to_srgb(straight), np.clip(rgba[..., 3], 0, 1)


def render(rgb, raw=None, alpha=None, preview=False, detail=False, cancel=lambda: None, mask_preview=False):
    s = settings(raw)
    rgb, alpha = prepare(rgb, s, alpha, preview, detail)
    h, w = rgb.shape[:2]
    rgba = np.concatenate((to_linear(rgb), alpha[..., None]), -1)
    encoded = to_srgb(rgba[..., :3]); padded = np.pad(encoded, ((0, max(0, 128-h)), (0, max(0, 128-w)), (0, 0)), mode="edge")
    with work_directory() as directory:
        input_path = Path(directory) / "input.tif"
        tifffile.imwrite(input_path, padded, photometric="rgb", metadata=None, extratags=[(34675, "B", len(color_profile()), color_profile(), False)])
        result = run_native(directory, input_path, s, cancel)[:h, :w, :3]
    result = color_tools.apply(result, alpha, s['colorMixer'], s['skinTone'], mask_preview, cancel)
    return np.ascontiguousarray(result, dtype=np.float32), np.ascontiguousarray(alpha, dtype=np.float32)


def png_bytes(pixels, depth, icc):
    h, w, channels = pixels.shape; output = io.BytesIO()
    def chunk(kind, data): output.write(struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data)))
    output.write(b"\x89PNG\r\n\x1a\n"); chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, 6 if channels == 4 else 2, 0, 0, 0))
    chunk(b"iCCP", b"sRGB\x00\x00" + zlib.compress(icc))
    compressor = zlib.compressobj(6)
    for row in pixels:
        data = compressor.compress(b"\x00" + row.astype(">u2" if depth == 16 else "u1").tobytes())
        if data: chunk(b"IDAT", data)
    chunk(b"IDAT", compressor.flush()); chunk(b"IEND", b"")
    return output.getvalue()


def export_bytes(rgb, alpha=None, fmt="png", depth=8, quality=95):
    settings(dict(format=fmt, bitDepth=depth, quality=quality))
    rgb = np.asarray(rgb, dtype=np.float32)
    if fmt == "jpeg" and alpha is not None:
        rgb = to_srgb(to_linear(rgb) * alpha[..., None] + 1 - alpha[..., None]); alpha = None
    rgb = to_linear(rgb) if depth == 32 else rgb
    pixels = np.concatenate((rgb, alpha[..., None]), -1) if alpha is not None else rgb
    if depth != 32: pixels = np.rint(np.clip(pixels, 0, 1) * (65535 if depth == 16 else 255)).astype(np.uint16 if depth == 16 else np.uint8)
    icc = color_profile(depth == 32)
    if fmt == "png": return png_bytes(pixels, depth, icc)
    output = io.BytesIO()
    if fmt == "tiff":
        tifffile.imwrite(output, pixels, photometric="rgb", metadata=None, compression="deflate", extratags=[(34675, "B", len(icc), icc, False)], **({"extrasamples": ["unassalpha"]} if alpha is not None else {}))
    else:
        Image.fromarray(pixels).save(output, format="JPEG" if fmt == "jpeg" else "WEBP", quality=quality, icc_profile=icc, **({"subsampling": 0} if fmt == "jpeg" else {}))
    return output.getvalue()
