import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import threading

from aiohttp import web
import numpy as np
from PIL import Image
from server import PromptServer

from .engine import export_bytes, find_engine, render, resize_float, settings, prepare, work_directory
from .nodes import input_path, read_file
from .state import CACHE, JOBS

BASE = "/famegrid/photo-pro"
routes = PromptServer.instance.routes
UPLOADS = threading.BoundedSemaphore(2)


@routes.get(BASE + "/editor/{file:.*}")
async def editor_file(request):
    # A normal GET route also receives ComfyUI's /api alias. RouteTableDef's
    # static resource does not support that alias in current ComfyUI releases.
    root = (Path(__file__).parent / "editor").resolve()
    path = (root / request.match_info["file"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix not in (".html", ".js", ".css", ".svg", ".woff2"):
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def respond(data=None, status=200, **kwargs):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"}, **kwargs)


async def job(request, fn):
    if not JOBS.acquire(blocking=False): raise web.HTTPTooManyRequests(text='{"error":"Photo processor is busy. Try again shortly."}', content_type="application/json")
    cancelled = threading.Event()
    def check():
        if cancelled.is_set(): raise RuntimeError("Photo processing cancelled.")
    async def monitor():
        while True:
            await asyncio.sleep(.15)
            if request.transport is None or request.transport.is_closing(): cancelled.set(); return
    watcher = asyncio.create_task(monitor())
    task = asyncio.create_task(asyncio.to_thread(fn, check))
    try: return await asyncio.shield(task)
    finally:
        cancelled.set(); watcher.cancel()
        # Keep admission reserved until the native worker has actually exited.
        try: await asyncio.shield(task)
        finally: JOBS.release()


def thumbnail(rgb, alpha):
    h, w = rgb.shape[:2]; scale = min(1, 1000 / max(w, h))
    rgba = resize_float(np.concatenate((rgb, alpha[..., None]), -1), max(1, int(w*scale)), max(1, int(h*scale)))
    return export_bytes(rgba[..., :3], np.clip(rgba[..., 3], 0, 1), "webp", 8, 90)


def metadata(key):
    entry = CACHE.get(key); rgb = entry["rgb"]
    return dict(id=key, width=rgb.shape[1], height=rgb.shape[0], displayPreview=base64.b64encode(thumbnail(rgb, entry["alpha"])).decode(), displayFormat="webp", inputFile=entry.get("inputFile"), sourceMode=entry.get("sourceMode", "incoming image"))


@routes.get(BASE + "/engine")
async def engine_status(request):
    try:
        if request.query.get("refresh"): find_engine.cache_clear()
        await asyncio.to_thread(find_engine)
        return respond(dict(ready=True, version="5.13", features=dict(directLibraryImport=True, directLibrarySave=False, colorTools=True)))
    except Exception as error: return respond(dict(ready=False, version="5.13", message=str(error), features=dict(colorTools=True)))


@routes.post(BASE + "/source")
async def source(request):
    if not UPLOADS.acquire(blocking=False): return respond(dict(error="Photo importer is busy. Try again shortly."), 429)
    try:
        if request.content_type == "application/json":
            body = await request.json()
            if not isinstance(body, dict): raise ValueError("Invalid source reference.")
            if body.get("last_node_id") is not None:
                previous = CACHE.get("last:" + str(body["last_node_id"]))
                key = CACHE.put(previous["rgb"], previous["alpha"], sourceMode="incoming image", name="Last incoming image")
            elif body.get("input_file"):
                path = input_path(body["input_file"])
                rgb, alpha = await job(request, lambda cancel: read_file(path, cancel))
                key = CACHE.put(rgb, alpha, inputFile=body["input_file"], sourceMode="uploaded image", name=path.name)
            else: raise ValueError("Select an uploaded file or last incoming image.")
        else:
            # ComfyUI's default multipart post limit is small; the reader below
            # enforces our own 32 MiB image limit while streaming.
            reader = await request.multipart(); part = await reader.next()
            if not part or part.name != "image": raise ValueError("Choose an image.")
            suffix = Path(part.filename or "").suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".avif", ".tif", ".tiff"): raise ValueError("Unsupported image format.")
            chunks = []; total = 0
            while True:
                chunk = await part.read_chunk(65536)
                if not chunk: break
                total += len(chunk)
                if total > 32 * 1024 * 1024: raise ValueError("Choose an image under 32 MB.")
                chunks.append(chunk)
            data = b"".join(chunks)
            def decode(cancel):
                with work_directory() as directory:
                    path = Path(directory) / ("upload" + suffix); path.write_bytes(data)
                    return read_file(path, cancel)
            rgb, alpha = await job(request, decode)
            key = CACHE.put(rgb, alpha, original=data, suffix=suffix, sourceMode="uploaded image", name=part.filename)
        previous = request.headers.get("X-Photo-Replace-Source")
        if previous and not previous.startswith(("last:", "output:")) and previous != key: CACHE.drop(previous)
        return respond(await asyncio.to_thread(metadata, key))
    except web.HTTPException: raise
    except Exception as error: return respond(dict(error=str(error)), 400)
    finally: UPLOADS.release()


@routes.get(BASE + "/last/{node_id}")
async def last(request):
    try:
        data = CACHE.get("last:" + request.match_info["node_id"])
        return respond(dict(available=True, width=data["rgb"].shape[1], height=data["rgb"].shape[0]))
    except ValueError: return respond(dict(available=False))


@routes.get(BASE + "/thumbnail/{node_id}")
async def thumb(request):
    try:
        data = CACHE.get(("last:" if request.query.get("before") == "1" else "output:") + request.match_info["node_id"])
        return web.Response(body=await asyncio.to_thread(thumbnail, data["rgb"], data["alpha"]), content_type="image/webp", headers={"Cache-Control": "no-store"})
    except ValueError: raise web.HTTPNotFound()


@routes.post(BASE + "/commit/{source_id}")
async def commit(request):
    """Only Apply persists the uploaded original; live edits never hit output/."""
    try:
        entry = CACHE.get(request.match_info["source_id"])
        if entry.get("sourceMode") != "uploaded image": return respond(dict(sourceMode="incoming image"))
        name = entry.get("inputFile")
        if not name:
            name = f"famegrid_photo_pro/{hashlib.sha256(entry['original']).hexdigest()}{entry['suffix']}"
            path = input_path(name); path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle: handle.write(entry["original"])
            except FileExistsError: pass
            entry["inputFile"] = name
        return respond(dict(sourceMode="uploaded image", inputFile=name))
    except Exception as error: return respond(dict(error=str(error)), 400)


@routes.delete(BASE + "/{source_id}")
async def delete_source(request):
    key = request.match_info["source_id"]
    if key.startswith(("last:", "output:")): return respond(dict(error="Invalid editing source."), 400)
    CACHE.drop(key); return respond(dict(ok=True))


@routes.post(BASE + "/{source_id}/render")
async def develop(request):
    try:
        source_id = request.match_info["source_id"]; entry = CACHE.get(source_id)
        body = await request.json(); config = settings(body.get("settings")); preview, detail = body.get("preview") is True, body.get("detail") is True
        mask_preview = body.get('mask_preview') is True
        if mask_preview and not preview: return respond(dict(error='Mask view is preview-only.'),400)
        version = json.dumps([config, detail, mask_preview], sort_keys=True)
        cached = entry["previews"].get(version) if preview and not detail else None
        fmt = "png" if detail else "webp" if preview else config["format"]
        depth = 8 if preview else config["bitDepth"]
        if cached is None:
            def process(cancel):
                pixels, opacity, native_config = entry["rgb"], entry["alpha"], config
                if preview and not detail:
                    geometry = json.dumps([config["crop"], config["rotation"], config["resize"]], sort_keys=True)
                    proxy = entry.get("proxy")
                    if not proxy or proxy[0] != geometry:
                        pixels, opacity = prepare(pixels, config, opacity, preview=True)
                        with CACHE.lock:
                            if source_id in CACHE.entries:
                                entry["proxy"] = (geometry, pixels, opacity)
                                if CACHE.total_size() > CACHE.limit: entry.pop("proxy", None)
                    else: pixels, opacity = proxy[1:]
                    native_config = dict(config, crop=dict(x=0, y=0, width=1, height=1), rotation=0, resize=None)
                rgb, alpha = render(pixels, native_config, opacity, preview=preview, detail=detail, cancel=cancel, mask_preview=mask_preview)
                data = export_bytes(rgb, alpha, fmt, depth, 92 if preview else config["quality"])
                hist = [np.histogram(np.rint(np.clip(rgb[..., c], 0, 1)*255)[alpha > 0], bins=256, range=(0, 256))[0].tolist() for c in range(3)]
                return data, hist
            data, histogram = await job(request, process)
            if preview and not detail: CACHE.remember(source_id, version, data)
        else:
            data = cached
            with Image.open(io.BytesIO(data)) as image:
                p = np.asarray(image.convert("RGB")); histogram = [np.bincount(p[..., c].ravel(), minlength=256).tolist() for c in range(3)]
        return web.Response(body=data, content_type="image/" + ("jpeg" if fmt == "jpeg" else fmt), headers={"Cache-Control": "no-store", "X-Photo-Histogram": json.dumps(histogram) if preview else "[]"})
    except web.HTTPException: raise
    except Exception as error: return respond(dict(error=str(error)), 400)
