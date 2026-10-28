# FameGrid Photo Pro

A custom-designed ComfyUI photo finishing node: a large image preview, a purple
FameGrid interface, and a live editor with the same controls as Creator Studio's
Photo Develop. Former working name: **FameGrid Color Pro**.

## Install

Repository: https://github.com/Elevenheights/ComfyUI-FameGridPhotoPro

### Standard ComfyUI / virtual environment

Open a terminal in the folder containing ComfyUI's `main.py`. Activate the same
Python environment you use to start ComfyUI, then run:

```sh
git clone https://github.com/Elevenheights/ComfyUI-FameGridPhotoPro.git custom_nodes/ComfyUI-FameGridPhotoPro
python -m pip install -r custom_nodes/ComfyUI-FameGridPhotoPro/requirements.txt
```

### Windows Portable

Open PowerShell in the outer portable folder containing `ComfyUI` and
`python_embeded`, then run:

```powershell
git clone https://github.com/Elevenheights/ComfyUI-FameGridPhotoPro.git .\ComfyUI\custom_nodes\ComfyUI-FameGridPhotoPro
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\ComfyUI-FameGridPhotoPro\requirements.txt
```

Without Git, choose **Code > Download ZIP** on GitHub. Extract and rename the
folder to `ComfyUI-FameGridPhotoPro` inside `ComfyUI/custom_nodes`. Check that
`__init__.py` sits directly inside it, rather than inside a second nested folder.
Then run the appropriate requirements command above.

### Set up the photo processor

Install the separate [RawTherapee 5.13 package](https://www.rawtherapee.com/downloads/5.13/)
and locate its **`rawtherapee-cli`** executable. Use the CLI executable, not the
desktop application's `rawtherapee.exe`.

The node searches PATH, the standard Windows 5.13 installation, and an existing
Creator Studio engine installation. If your executable is elsewhere, set
`FAMEGRID_RAWTHERAPEE_PATH` **on the computer running ComfyUI**, before starting it.
Windows PowerShell example (replace the path with your actual installation):

```powershell
$env:FAMEGRID_RAWTHERAPEE_PATH = 'C:\Program Files\RawTherapee\5.13\rawtherapee-cli.exe'
& $env:FAMEGRID_RAWTHERAPEE_PATH -v
```

Start ComfyUI from that same terminal: use `python main.py` for a standard
installation, or your usual portable launcher, such as `.\run_nvidia_gpu.bat`.
To make the setting
persistent for future terminals, use:

```powershell
[Environment]::SetEnvironmentVariable('FAMEGRID_RAWTHERAPEE_PATH', 'C:\Program Files\RawTherapee\5.13\rawtherapee-cli.exe', 'User')
```

For Linux or RunPod, install a compatible RawTherapee CLI on the server and make
it discoverable with `command -v rawtherapee-cli`, or set its actual path in the
ComfyUI startup script:

```sh
export FAMEGRID_RAWTHERAPEE_PATH=/path/to/rawtherapee-cli
python main.py
```

Restart ComfyUI after installation, then refresh its browser page. Search for
**FameGrid Photo Pro** in the node menu. Open **Adjust Settings/Edit Photo**; the
editor reports a missing engine if the CLI cannot be found. Python dependencies
must be installed into ComfyUI's interpreter, not an unrelated system Python.
PyTorch comes from ComfyUI; this package does not install or replace it.

The download includes the compiled editor. **No Node.js, Creator Studio server,
API key, model download or generation credits are required.** RawTherapee runs
locally on the CPU; its engine is a separate dependency.

## FAMEGRID ULTRAREAL setup

1. Load your workflow and select **FameGrid Photo Pro** in its **PHOTO FINISH**
   route panel. The unused Camera Raw path is muted.
2. Click **Adjust Settings/Edit Photo** to prepare settings, even before an image
   is available. Choose **Apply to node** to store them.
3. Run the workflow once to receive an image, then reopen the editor to inspect
   its full-resolution preview. Apply changes and queue again for the saved output.
4. To use the alternative finish, select **Camera Raw** in the finish panel. If
   Photo Pro is not installed, load the separate **Camera Raw** workflow edition
   to avoid a missing custom-node placeholder.

The **Reference to Image** and **Image to Image** workflows also use the separate
[Grok Chat node](https://github.com/Elevenheights/ComfyUI-Grok-Chat#install-and-setup).
**True Text to Image** uses your written prompt and does not require Grok.

## Update and troubleshooting

From the ComfyUI folder:

```sh
git -C custom_nodes/ComfyUI-FameGridPhotoPro pull --ff-only
python -m pip install -r custom_nodes/ComfyUI-FameGridPhotoPro/requirements.txt
```

Portable users should use their embedded Python and prepend `ComfyUI/` to the
paths when running from the outer portable folder. Restart ComfyUI and refresh
the browser after updating. Wait for active jobs to finish before restarting.

- **Node missing:** inspect the ComfyUI terminal for import errors and verify
  the folder layout and interpreter used for dependency installation.
- **Processor missing:** verify `FAMEGRID_RAWTHERAPEE_PATH` points to the CLI
  executable on the server. A browser computer's installation cannot supply a
  remote RunPod server's engine.
- **Editor looks outdated or blank:** hard-refresh the browser after restarting;
  the repository must include `editor/index.html` and its `editor/assets` files.

Join our [Discord](https://discord.gg/hXmy4zVsEq) for support, updates and early access.

## Use

Add **FameGrid → Photo → FameGrid Photo Pro**.

- **Settings first:** click **Adjust Settings/Edit Photo** before loading an image.
  Set light, color, detail, sharpening and grain, then **Apply to node** to save
  them in the workflow. The first photo opened keeps those prepared settings.
- **Connected image:** connect an IMAGE and optionally its MASK, then queue the
  workflow once. Click **Adjust Settings/Edit Photo**. The editor opens the
  original incoming image from the latest execution, not the already-adjusted
  result, so changes do not compound. For batches it previews the first image;
  the node processes every frame with the same saved settings.
- **Uploaded file:** open the editor even without an IMAGE connection, choose
  **Open image**, then adjust the uploaded file. Click **Apply to node**. The
  original is saved to `ComfyUI/input/famegrid_photo_pro`, and its reference is
  stored in the workflow. Queue to produce the processed IMAGE/MASK outputs.
- **Switch back:** **Use last incoming image** switches from an upload to the
  last connected input, when available. Subsequent queues always process the
  new incoming image; the cached preview is not substituted into the workflow.
- **Apply to node** saves settings and source choice into the graph. **Cancel**
  leaves the node unchanged. Live previews do not queue the workflow.
- The `enabled` switch bypasses all adjustments, geometry and native rendering.

The live editor includes light, color, texture, clarity, dehaze, native USM/RL
sharpening, film grain, histogram, before/after, undo/redo,
mouse-wheel zoom, drag-to-pan, zoom slider, Fit and full-screen viewing.
Slider previews debounce for 350 ms and always return the full output resolution.
Edits retain zoom and pan; inspection never crops the image. Temporary sources
are released when the editor closes, with bounded caches and expiry as a fallback.

## Color Mixer and Skin Tone (0.2.0)

Both sections start disabled so existing workflows and presets keep their
previous appearance. They use float32 HSL processing after the RawTherapee
develop step; they are not RawTherapee HSV curves.

**Color Mixer:** enable it, select red, orange, yellow, green, aqua, blue, purple
or magenta, then adjust that channel's Hue, Saturation and Lightness. Channels
blend into neighboring hues. Neutral grays stay protected. Hue spans up to 30
degrees either way; saturation and lightness move toward their lower/upper
limits. **Reset selected color** only resets that channel.

**Skin Tone:** enable it and use Hue, Saturation and Lightness for the selected
area. Turn on **Show skin selection mask** to inspect it: white is selected,
black is protected, gray is partial strength. Refine Target hue, Hue range,
Color feather, minimum saturation and the lightness limits. To protect similar
colors elsewhere, reduce the selection area's Width/Height, then position it
with Left/Top percentages. Area edges are feathered automatically. Coordinates
refer to the current cropped/rotated output image.

This is an adjustable color-and-area selection, not automatic skin recognition.
Similar-colored clothing inside the selected area can still be selected; check
the mask before applying. Mask view is preview-only: **Apply to node**, native
node execution and image exports use the color photograph, never the grayscale
selection preview. **Reset skin tone** restores the selection and adjustments.

The desktop app and ComfyUI node share the new settings JSON. Older presets get
neutral defaults. The separate Creator Studio app does not expose these new
controls yet. Restart ComfyUI and refresh the browser after updating; an older
running backend cannot apply the new tools until restarted.

## Precision and saving

IMAGE outputs are float32 **sRGB-encoded**, matching ComfyUI's IMAGE convention;
geometry/resampling operates in linear-light float32. Native input/output uses
float32 TIFFs without an 8-bit handoff. MASK outputs use ComfyUI's convention:
`0 = opaque`, `1 = transparent`. Connected masks must match the image dimensions;
one mask can broadcast across a batch.

Connect **FameGrid Photo Pro · Save** for:

| Format | Supported bit depths |
| --- | --- |
| PNG | 8 (default), 16 |
| JPEG / WebP | 8 |
| TIFF | 8, 16, 32-bit float |

Integer exports are tagged sRGB and quantized only at final encoding. Float32
TIFF masters are linear sRGB with a matching ICC profile. JPEG composites alpha
over white in linear light before encoding; PNG/TIFF/WebP can retain alpha.
The standard ComfyUI Save Image node remains usable, with its own output-depth
behavior. TIFFs are saved to disk; use a Preview Image node to view those pixels.
Export is limited to 16 megapixels. Camera RAW, animation and multipage inputs
are not supported; uploads are limited to 32 MiB / 60 million source pixels.

The example workflow in `example_workflows/photo-pro.json` connects Load Image,
Photo Pro, Preview Image and the optional precision-aware save node. Replace
`example.png` in Load Image with your own image.

## Storage and interruption

- View changes do not upload or process pixels.
- Sources, proxies, last inputs and fit-result caches share a bounded 384 MiB
  memory budget and a 32-entry limit. Shared last-input/source arrays count once.
  They expire after 45 minutes idle, including when no new requests arrive.
- Closing the editor deletes its temporary editing source. Interrupted native
  jobs terminate; job folders are removed after success/failure/cancellation.
  Only marked abandoned jobs from dead processes are reaped after a crash.
- Cancelled file edits do not save originals. Apply persists a content-addressed
  original (the same file deduplicates), not a new image for each slider change.
  Those originals are intentional workflow inputs: remove unused ones through
  normal ComfyUI input-file management. They are not automatically deleted from
  under saved workflows. The save node creates output files only when queued.
- Two native jobs and two streaming uploads are admitted at a time. Previews
  cancel when the editor connection closes. Native processing has a 90 s limit.

This extension uses ComfyUI's existing server access controls; it adds no remote
service or account system. On a shared ComfyUI server, last inputs belong to that
server/workflow node, not to a separate Creator Studio account.

## Development / delivery status

The Python package and compiled editor are standalone. The editor source lives
in Creator Studio's `comfyui/editor-src` and reuses its Photo Develop panel.
Rebuild there with `node scripts/build-famegrid-photo-pro.mjs`.

This is a GitHub prerelease, not a ComfyUI Registry publication. The package
retains its existing proprietary license metadata; publication does not assign
an open-source license. See `THIRD_PARTY_NOTICES.md` for bundled runtime notices.
Windows validation covers native float processing and exports, masks/batches,
interruption and cleanup, both live-editor source modes, and a real ComfyUI
workflow queue/save plus the actual custom-node UI. Linux/macOS engine/runtime
validation remains to be done before claiming support for those editions.
RawTherapee remains separately distributed under its own GPL license/notices;
this adapter invokes its CLI and writes PP3 profiles rather than embedding its
processing code.
