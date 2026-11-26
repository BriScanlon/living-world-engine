# ComfyUI workflow templates

These are ComfyUI **API-format** graphs (the format produced by *Workflow → Export (API)*),
with two extra documentation keys the VTT strips before submitting:

| Key         | Purpose                                                                 |
|-------------|-------------------------------------------------------------------------|
| `_comment`  | Human notes — node-by-node description of the graph.                    |
| `_params`   | Map of **VTT parameter → `{node, input}`**. The VTT overwrites exactly these inputs for every job. |

Each node's `_meta.title` is prefixed with its node ID (e.g. `[6] POSITIVE PROMPT <- image_prompt`),
so the injection points are obvious when you load the file into the ComfyUI editor too.

## `flux_battlemap.json` — text → 4K base map

| Param            | Node | Input             | Set by the VTT                                      |
|------------------|------|-------------------|-----------------------------------------------------|
| `prompt`         | 6    | `text`            | LLM `image_prompt` + enforced top-down/scale suffix |
| `seed`           | 9    | `noise_seed`      | random per job                                       |
| `width`/`height` | 8    | `width`/`height`  | 1920×1088 for maps, 1024×1024 for tokens             |
| `target_width`/`target_height` | 17 | `width`/`height` | 3840×2160 for maps, 512×512 for tokens  |
| `filename_prefix`| 18   | `filename_prefix` | `vtt/<asset id>`                                     |

Models expected (rename in the JSON if yours differ):

- `models/diffusion_models/flux1-dev.safetensors`
- `models/text_encoders/t5xxl_fp8_e4m3fn.safetensors`, `models/text_encoders/clip_l.safetensors`
- `models/vae/ae.safetensors`
- `models/upscale_models/4x-UltraSharp.pth` (any 4× ESRGAN-family model works; or drop nodes 15–16)

## `wan21_flf2v_loop.json` — still → seamless looping video

The still image is uploaded to ComfyUI (`POST /upload/image`) and used as **both** first and last
frame. Combined with a locked-camera motion prompt and a strong camera-motion negative prompt, Wan 2.1
FLF2V returns to the starting frame, and node 16 drops the duplicated final frame so `<video loop>`
has no visible seam.

| Param            | Node | Input             | Set by the VTT                                     |
|------------------|------|-------------------|----------------------------------------------------|
| `first_frame`    | 5    | `image`           | uploaded file name                                  |
| `last_frame`     | 6    | `image`           | same uploaded file name                             |
| `motion_prompt`  | 10   | `text`            | LLM / vision-LLM motion prompt (camera-lock prefix) |
| `seed`           | 14   | `seed`            | random per job                                      |
| `width`/`height` | 12   | `width`/`height`  | source aspect, longest side 1280, multiples of 16   |
| `filename_prefix`| 17   | `filename_prefix` | `vtt/<asset id>_loop`                               |

Models expected:

- `models/diffusion_models/wan2.1_flf2v_720p_14B_fp16.safetensors` (or the `fp8_e4m3fn` variant)
- `models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- `models/vae/wan_2.1_vae.safetensors`
- `models/clip_vision/clip_vision_h.safetensors`

All are published in the Comfy-Org "repackaged" model repos on Hugging Face.

## Using your own graph

1. Build/tune the graph in ComfyUI, then *Export (API)*.
2. Add a `_params` block mapping the VTT parameter names above to your node IDs/inputs
   (a param may also map to a list of `{node, input}` targets).
3. Save it in this folder and point `COMFY_IMAGE_WORKFLOW` / `COMFY_VIDEO_WORKFLOW` in `.env` at it.

Unknown params are ignored; a `_params` entry that points at a node that doesn't exist fails the job
with a clear error. The VTT accepts any image output from the image workflow and any
`.mp4/.webm/.mov/.mkv` (or animated `.webp/.gif`) from the video workflow — so `SaveVideo`,
`SaveAnimatedWEBP` or VideoHelperSuite's `VHS_VideoCombine` all work as the final node.
