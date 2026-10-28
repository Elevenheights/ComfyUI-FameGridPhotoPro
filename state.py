"""Bounded transient originals, last inputs and previews; no rendered edit files."""
from collections import OrderedDict
import threading
import time
import uuid

import numpy as np


class Cache:
    def __init__(self, limit=384 * 1024 * 1024, ttl=2700):
        self.limit, self.ttl = limit, ttl
        self.entries = OrderedDict(); self.lock = threading.RLock()

    def _sweep(self):
        now = time.monotonic()
        for key, value in list(self.entries.items()):
            if value["expires"] <= now: del self.entries[key]

    @staticmethod
    def size(value):
        proxy = value.get("proxy")
        return value["rgb"].nbytes + value["alpha"].nbytes + len(value.get("original", b"")) + sum(len(v) for v in value.get("previews", {}).values()) + (proxy[1].nbytes + proxy[2].nbytes if proxy else 0)

    def put(self, rgb, alpha, key=None, **meta):
        with self.lock:
            self._sweep(); key = key or uuid.uuid4().hex
            value = dict(rgb=rgb, alpha=alpha, expires=time.monotonic() + self.ttl, previews=OrderedDict(), **meta)
            if self.size(value) > self.limit: raise ValueError("This image exceeds the editor's cache budget.")
            self.entries.pop(key, None)
            self.entries[key] = value
            while len(self.entries) > 32 or self.total_size() > self.limit: self.entries.popitem(last=False)
            return key

    def total_size(self):
        # Editor sources can share the last incoming immutable arrays; count
        # that storage once instead of evicting the source on opening it.
        objects = {}
        for value in self.entries.values():
            for data in (value["rgb"], value["alpha"], value.get("original", b""), *value.get("previews", {}).values(), *(value["proxy"][1:] if value.get("proxy") else ())):
                objects[id(data)] = data.nbytes if isinstance(data, np.ndarray) else len(data)
        return sum(objects.values())

    def get(self, key):
        with self.lock:
            self._sweep()
            value = self.entries.get(key)
            if value is None: raise ValueError("Source expired. Reopen the file or run the upstream node again.")
            value["expires"] = time.monotonic() + self.ttl; self.entries.move_to_end(key)
            return value

    def drop(self, key):
        with self.lock: self.entries.pop(key, None)

    def remember(self, key, version, data):
        with self.lock:
            entry = self.entries.get(key)
            if not entry or len(data) > 2 * 1024 * 1024: return
            previews = entry["previews"]; previews[version] = data
            while len(previews) > 3: previews.popitem(last=False)
            if self.total_size() > self.limit: previews.clear()

    def sweep(self):
        with self.lock: self._sweep()


CACHE = Cache()
JOBS = threading.BoundedSemaphore(2)


def _sweep():
    CACHE.sweep()
    from .engine import cleanup_abandoned_jobs
    cleanup_abandoned_jobs()
    timer = threading.Timer(60, _sweep); timer.daemon = True; timer.start()

_sweep()


def tensor_arrays(image, mask=None):
    pixels = image.detach().to(device="cpu", dtype=__import__("torch").float32).numpy()
    if pixels.ndim != 4 or not pixels.shape[0] or pixels.shape[-1] not in (3, 4) or pixels.shape[1] * pixels.shape[2] > 60_000_000 or not np.isfinite(pixels).all(): raise ValueError("Expected finite ComfyUI IMAGE pixels [batch, height, width, 3/4].")
    rgb = pixels[..., :3]
    alpha = pixels[..., 3] if pixels.shape[-1] == 4 else np.ones(pixels.shape[:3], np.float32)
    if mask is not None:
        masks = mask.detach().to(device="cpu", dtype=__import__("torch").float32).numpy()
        if masks.ndim == 2: masks = masks[None]
        if masks.ndim != 3 or masks.shape[0] not in (1, pixels.shape[0]) or not np.isfinite(masks).all(): raise ValueError("Invalid MASK batch.")
        if masks.shape[1:] != pixels.shape[1:3]:
            # Stock Load Image returns a 64×64 all-zero placeholder for an
            # opaque image in some ComfyUI versions, regardless of image size.
            if np.any(masks): raise ValueError("Nonempty MASK dimensions must match the IMAGE.")
        else: alpha = alpha * (1 - np.clip(np.broadcast_to(masks, pixels.shape[:3]), 0, 1))
    return rgb, np.clip(alpha, 0, 1)
