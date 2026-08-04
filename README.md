# runpod-h3

Drive [MiniMax-H3](https://www.minimax.io/blog/minimax-h3) (video + native audio in one
pass) on a RunPod ComfyUI pod, from Claude Code — including from the phone with the
laptop off.

The `h3-video` skill in `.claude/skills/` is the entry point. Open this repo in a Claude
Code session and say *"make an H3 video about …"*.

## One-time setup

**1. RunPod connector** — claude.ai → Settings → Connectors → Add custom connector →
`https://mcp.getrunpod.io/`, then sign in to RunPod. This is what lets Claude find,
start, and stop pods. OAuth, so no API key is stored anywhere.

**2. A pod.** The connector's `create-pod` tool can't attach a network volume or set a
minimum CUDA version, so the pod has to be created once from the console or `runpodctl`.

```bash
runpodctl pod create --name minimax-h3 \
  --image "hearmeman/comfyui-minimax-template:v2" \
  --gpu-id "NVIDIA RTX PRO 6000 Blackwell Server Edition" --gpu-count 1 \
  --container-disk-in-gb 50 \
  --network-volume-id YOUR_VOLUME_ID --data-center-ids EUR-IS-1 \
  --min-cuda-version 13.0 \
  --ports "8188/http,8888/http" \
  --env '{"download_minimax_h3":"true"}' --cloud-type SECURE
```

`download_minimax_h3=true` is not optional — without it the pod boots a healthy ComfyUI
with no models and the workflows look broken. Budget ~100 GB of network volume: the
int8 weights are ~70 GB.

In the console instead: the CUDA filter lives under **Additional filters → CUDA
Versions**, which only exists in the *legacy* deploy flow. The early-access flow has a
**Filter** button in the Compute panel.

## Cost

| | |
|---|---|
| RTX PRO 6000, running | ~$1.99/hr |
| Network volume, 100 GB | ~$7/month, billed whether or not the pod runs |

Stopping the pod keeps the volume, so restarts take ~1 min instead of re-downloading
70 GB. **Terminating** the volume destroys the weights. The pod bills continuously while
running — no Claude session is required for that, and closing the app stops nothing.
[console.runpod.io](https://console.runpod.io) is the source of truth.

## Manual use

```bash
python3 .claude/skills/h3-video/scripts/h3.py status POD_ID
python3 .claude/skills/h3-video/scripts/h3.py wait   POD_ID
python3 .claude/skills/h3-video/scripts/h3.py run    POD_ID --prompt "…" --seconds 8 --out out
```

## Known quirks

- RunPod's proxy returns **403** for the default `Python-urllib` User-Agent. The script
  sends `curl/8.7.1`.
- H3 frame counts must satisfy `length ≡ 5 (mod 17)`. The script rounds for you.
- The workflows shipped in the template are UI-format and use a subgraph, so they can't
  be POSTed to `/prompt`. The script builds the API-format graph directly instead.
- 768p is the ceiling for the open weights; 4–15 second clips.
- The model does not honor timeline instructions ("0:00–0:04 …"). Render separate clips.

## API-format workflows

`workflows/minimax_h3_t2v_api.json` and `..._i2v_api.json` are ComfyUI **API-format**
graphs (the shape `/prompt` accepts, and the shape a serverless
`input.workflow` payload needs). The template's own workflows are UI-format and use a
subgraph, so they cannot be POSTed directly — these were built against `/object_info`
instead, and validated against a live pod.

Placeholders to substitute before submitting:

| Placeholder | Node | Meaning |
|---|---|---|
| `PROMPT_GOES_HERE` | 104 | the prompt; describe the audio too |
| `INPUT_IMAGE.png` | 200 (i2v only) | filename returned by `/upload/image` |
| `noise_seed` | 15 | set for reproducibility, randomize otherwise |
| `length` | 104 | frames, must be ≡ 5 (mod 17) — 2s=56, 4s=107, 8s=192 |
| `width`/`height` | 104 | 768p ceiling; 16:9 is 1344x768 |
| `steps` | 9 | 20 is the template default |

Submit directly:

```bash
curl -X POST "https://POD_ID-8188.proxy.runpod.net/prompt" \
  -H 'Content-Type: application/json' \
  -d "{\"prompt\": $(cat workflows/minimax_h3_t2v_api.json)}"
```

Note `LoadImage` in the i2v graph will fail schema validation offline: its allowed
values are whatever images are currently uploaded to that server, so the list is empty
until you upload one.

## Web UI

`ui/index.html` is a single self-contained page — no build step, no dependencies.
Open it directly (`open ui/index.html`) or host it anywhere static.

It talks to ComfyUI from **your browser**, which is not subject to the sandbox network
restrictions that block Claude's cloud sessions. The pod template launches ComfyUI with
`--enable-cors-header *`, so cross-origin calls are allowed.

- **Workflow selector** — Text → Video, Image → Video, Reference → Video

Reference → Video accepts images, videos and audio clips, with the model card's limits
enforced in the browser before anything is uploaded:

| Input | Limit |
|---|---|
| Images | ≤ 9 |
| Videos | ≤ 3 clips, 2–15 s each, ≤ 15 s total |
| Audio | ≤ 3 clips, 2–15 s each, ≤ 15 s total, never the only input |
| All types | ≤ 12 files |

Durations are probed client-side, so an out-of-range clip is rejected before it costs
GPU time. A reference video is decoded on the pod via `LoadVideo` → `GetVideoComponents`,
which yields both the frames and the soundtrack; a checkbox controls whether that
soundtrack is passed as reference audio.

> **Untested:** the video and audio reference path has been validated structurally
> (graph shape, node wiring, output indices) but has not yet run on a live pod. The
> image path is confirmed working.
- **Live progress** — step-by-step over ComfyUI's websocket
- **Output** — inline player plus download, with seed and timing metadata

### What the page deliberately cannot do

**Start/stop the pod, or show uptime, cost, or balance.** All of those need an
authenticated call to RunPod's API, and that is impossible from a browser: RunPod
answers the CORS preflight with a non-2xx status (301 on `/v2`, 400 on `/v1`), so any
request carrying an `Authorization` header is blocked before it is sent. Verified
empirically — the same URL returns 401 without the header and throws with it.

Rendering is unaffected, because ComfyUI on the pod sends proper CORS headers and needs
no authentication.

For pod control use the [RunPod console](https://console.runpod.io/pods), or ask Claude
(the RunPod MCP connector calls the API server-side, where CORS does not apply).

Making this work in-page would need a small proxy holding the API key — a Cloudflare
Worker or similar — which is a deliberate piece of infrastructure, not a page tweak.
