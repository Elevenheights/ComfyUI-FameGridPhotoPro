import json
from pathlib import Path
import uuid

import numpy as np

from .engine import export_bytes, render, settings
from .state import CACHE, JOBS, tensor_arrays


def interrupt():
    from comfy.model_management import throw_exception_if_processing_interrupted
    throw_exception_if_processing_interrupted()


def input_path(name):
    import folder_paths
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / name).resolve()
    managed = (root / "famegrid_photo_pro").resolve()
    if not path.is_relative_to(managed) or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff"):
        raise ValueError("Choose a file uploaded through the FameGrid editor.")
    return path


def read_file(path, cancel=lambda: None):
    from PIL import Image
    from .engine import run_native, work_directory
    if path.stat().st_size > 32 * 1024 * 1024: raise ValueError("Choose an image under 32 MB.")
    alpha = None
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile
        with tifffile.TiffFile(path) as tif:
            if len(tif.pages) != 1: raise ValueError("Choose a single-frame TIFF.")
            page = tif.pages[0]; h, w = page.imagelength, page.imagewidth
            orientation = page.tags[274].value if 274 in page.tags else 1
            if page.extrasamples and page.shape[-1] == 4:
                raw_alpha = page.asarray()[..., 3]
                alpha = raw_alpha.astype(np.float32) / (np.iinfo(raw_alpha.dtype).max if raw_alpha.dtype.kind in "ui" else 1)
    else:
        with Image.open(path) as im:
            w, h = im.size
            if getattr(im, "n_frames", 1) != 1: raise ValueError("Choose a single-frame image.")
            alpha = np.asarray(im.getchannel("A"), dtype=np.float32) / 255 if "A" in im.getbands() else None
            orientation = im.getexif().get(274, 1); fmt = im.format
        if fmt == "PNG":
            import png
            with path.open("rb") as handle:
                pw, ph, rows, meta = png.Reader(file=handle).asDirect()
                if meta.get("alpha") and meta["bitdepth"] == 16:
                    pixels = np.vstack([np.asarray(row, dtype=np.uint16) for row in rows]).reshape(ph, pw, meta["planes"])
                    alpha = pixels[..., -1].astype(np.float32) / 65535
    if w * h > 60_000_000: raise ValueError("Choose an image under 60 megapixels.")
    with work_directory() as directory:
        rgb = run_native(directory, path, {}, cancel)
    if alpha is not None:
        if orientation in (5, 6, 7, 8): alpha = np.rot90(alpha, -1 if orientation in (5, 6) else 1)
        if orientation in (2, 5, 7): alpha = np.fliplr(alpha)
        if orientation in (3, 4): alpha = np.flipud(alpha)
        if orientation == 3: alpha = np.fliplr(alpha)
        if alpha.shape != rgb.shape[:2]: raise ValueError("Unsupported alpha orientation.")
    else: alpha = np.ones(rgb.shape[:2], np.float32)
    return rgb, np.ascontiguousarray(alpha)


class FameGridPhotoPro:
    CATEGORY = "FameGrid/Photo"
    FUNCTION = "develop"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "transparency")
    DESCRIPTION = "Light, color, HSL Color Mixer, masked Skin Tone, grain and sharpening. Click Adjust Settings/Edit Photo for live preview."
    SEARCH_ALIASES = ["FameGrid Color Pro", "Camera Raw", "Photo Develop", "HSL", "Skin Tone"]

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "enabled": ("BOOLEAN", {"default": True}),
            "source_mode": (["incoming image", "uploaded image"],),
            "input_file": ("STRING", {"default": ""}),
            "settings_json": ("STRING", {"default": "{}", "multiline": True}),
        }, "optional": {"image": ("IMAGE",), "mask": ("MASK",)}, "hidden": {"unique_id": "UNIQUE_ID"}}

    def develop(self, enabled=True, source_mode="incoming image", input_file="", settings_json="{}", image=None, mask=None, unique_id=""):
        import torch
        s = settings(settings_json)
        if source_mode not in ("incoming image", "uploaded image"): raise ValueError("Invalid source mode.")
        if image is not None:
            incoming_rgb, incoming_alpha = tensor_arrays(image, mask)
            if incoming_rgb[0].nbytes + incoming_alpha[0].nbytes <= CACHE.limit:
                CACHE.put(incoming_rgb[0].copy(), incoming_alpha[0].copy(), key=f"last:{unique_id}", sourceMode="incoming image", name="Last incoming image")
        if source_mode == "uploaded image":
            rgb, alpha = read_file(input_path(input_file), interrupt); rgb, alpha = rgb[None], alpha[None]
        elif image is not None: rgb, alpha = incoming_rgb, incoming_alpha
        else: raise ValueError("Connect an IMAGE or click Edit photo, upload a file and Apply.")
        output, masks = [], []
        for frame, opacity in zip(rgb, alpha):
            interrupt()
            if enabled:
                with JOBS: result, opacity = render(frame, s, opacity, cancel=interrupt)
            else: result = frame.copy(); opacity = opacity.copy()
            output.append(torch.from_numpy(np.ascontiguousarray(result, dtype=np.float32)))
            masks.append(torch.from_numpy(np.ascontiguousarray(1 - opacity, dtype=np.float32)))
        from .engine import resize_float
        first = output[0].numpy(); h, w = first.shape[:2]; factor = min(1, 1000 / max(w, h))
        thumb = resize_float(np.concatenate((first, (1-masks[0].numpy())[..., None]), -1), max(1, int(w*factor)), max(1, int(h*factor)))
        CACHE.put(thumb[..., :3].copy(), thumb[..., 3].copy(), key=f"output:{unique_id}", name="Developed image")
        try:
            from server import PromptServer
            PromptServer.instance.send_sync("famegrid.photo-pro.updated", {"node_id": str(unique_id)})
        except (ImportError, AttributeError): pass
        return torch.stack(output), torch.stack(masks)


class FameGridPhotoProSave:
    CATEGORY = "FameGrid/Photo"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_files",)
    OUTPUT_NODE = True
    DESCRIPTION = "Save tagged sRGB 8/16-bit images or linear-sRGB float32 TIFF masters. Processing precision is unchanged."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",), "filename_prefix": ("STRING", {"default": "FameGrid/PhotoPro"}), "format": (["png", "jpeg", "webp", "tiff"],), "bit_depth": (["8-bit", "16-bit", "32-bit float"],), "quality": ("INT", {"default": 95, "min": 50, "max": 100})}, "optional": {"mask": ("MASK",)}}

    def save(self, image, filename_prefix="FameGrid/PhotoPro", format="png", bit_depth="8-bit", quality=95, mask=None):
        import folder_paths
        depth = {"8-bit": 8, "16-bit": 16, "32-bit float": 32}.get(bit_depth)
        settings(dict(format=format, bitDepth=depth, quality=quality))
        rgb, alpha = tensor_arrays(image, mask)
        root = Path(folder_paths.get_output_directory()).resolve()
        prefix = (root / filename_prefix).resolve()
        if not prefix.is_relative_to(root) or prefix == root: raise ValueError("Filename prefix must stay inside ComfyUI/output.")
        prefix.parent.mkdir(parents=True, exist_ok=True)
        saved, previews = [], []
        for i, frame in enumerate(rgb):
            interrupt(); path = prefix.parent / f"{prefix.name}_{uuid.uuid4().hex[:12]}_{i:04d}.{'jpg' if format == 'jpeg' else format}"
            data = export_bytes(frame, alpha[i] if mask is not None or image.shape[-1] == 4 else None, format, depth, quality)
            created = False
            try:
                with path.open("xb") as handle:
                    created = True
                    handle.write(data)
            except BaseException:
                if created: path.unlink(missing_ok=True)
                raise
            saved.append(str(path))
            if format != "tiff": previews.append(dict(filename=path.name, subfolder=str(path.parent.relative_to(root)), type="output"))
        return {"ui": {"images": previews}, "result": (json.dumps(saved),)}


NODE_CLASS_MAPPINGS = {"FameGridPhotoPro": FameGridPhotoPro, "FameGridPhotoProSave": FameGridPhotoProSave}
NODE_DISPLAY_NAME_MAPPINGS = {"FameGridPhotoPro": "FameGrid Photo Pro", "FameGridPhotoProSave": "FameGrid Photo Pro · Save"}
