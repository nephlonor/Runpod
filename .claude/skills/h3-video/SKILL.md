---
name: h3-video
description: Generate video with native audio using MiniMax-H3 on a RunPod ComfyUI pod. Use when the user asks to create, make, or render a video or clip with H3/MiniMax, or asks to start/stop/check their video pod. Handles finding or starting the pod, waiting for ComfyUI, rendering, and delivering the file.
---

# MiniMax-H3 video on RunPod

Renders video **with a native 32 kHz stereo soundtrack in the same pass** — dialogue,
room tone, effects. There is no separate TTS or lip-sync stage.

Requires the **RunPod connector** (`https://mcp.getrunpod.io/`). If its tools aren't
available, stop and tell the user to authorize it in claude.ai → Settings → Connectors.

## Cost — read before doing anything

The pod bills **~$2/hr while RUNNING**, whether or not anything is rendering, and
nothing stops it automatically. The user's standing instruction is that the pod stays
up until they say otherwise, so **never stop or terminate it on your own initiative**.

- Stopping a pod is safe: the network volume keeps the ~70 GB of weights, so a restart
  takes ~1 min instead of re-downloading.
- **Never terminate/delete** the pod or its network volume unless the user explicitly
  asks. Deleting the volume destroys the weights.
- When a render finishes and the user hasn't said to keep going, remind them the pod
  is still billing and offer to stop it. Offer — don't act.

## Step 1 — Find the pod

Use the RunPod connector's `list-pods` tool. Identify the H3 pod by its image
(`hearmeman/comfyui-minimax-template:*`), not by a hardcoded ID — IDs change every
time a pod is recreated.

| State | Action |
|---|---|
| `RUNNING` | Go to step 2. |
| `EXITED` | `start-pod`, then step 2. Takes ~1 min with the volume attached. |
| No such pod | Stop. Creating one needs a network volume and a CUDA 13 host, which the connector's `create-pod` cannot set (no `networkVolumeId`, no CUDA filter). Tell the user to deploy from the console or `runpodctl` — see README.md. |

## Step 2 — Wait for ComfyUI

A `RUNNING` pod does not mean ComfyUI is listening. Port 8188 returns 502 until it is.

```bash
python3 .claude/skills/h3-video/scripts/h3.py wait POD_ID
```

If this fails immediately with a DNS or connection error rather than a 502, the sandbox
may not have egress to `*.proxy.runpod.net` — see "If there's no egress" below.

If it never becomes ready, read the boot logs with the connector's `stream-pod-logs`.
A healthy boot shows `CUDA Version 13.x`, `SageAttention wheel kernel probe passed`,
`[provisioner] ... quant: int8`, then the HF download manager finishing 5 files.

## Step 3 — Render

```bash
python3 .claude/skills/h3-video/scripts/h3.py run POD_ID \
  --prompt "…" --seconds 8 --out out
```

Defaults: 1344x768 (768p), 20 steps, 24 fps, random seed. The script prints progress,
then downloads the mp4 and prints its path.

Useful flags: `--width/--height`, `--steps`, `--seed` (reproducibility), `--image FILE`
for image-to-video, `--seconds`.

**Measured timings** (RTX PRO 6000, 20 steps): 8s at 1344x768 ≈ **250s**; 2.3s at
864x480 ≈ **110s**. Longer clips, more steps, or a busier GPU push this up, and the
foreground command limit is 10 minutes — so **run renders in the background** and poll.
Do not pipe the command through `tail`/`head` when backgrounding: that buffers
everything and you lose all progress output until it exits.

## Step 4 — Deliver

Send the mp4 to the user with the file-sending tool. Then state the pod is still
running and offer to stop it.

## Constraints that will bite you

- **Resolution: 768p max.** The open weights top out there. Anything advertising 2K is
  upscaling. 16:9 at 768p is 1344x768.
- **Duration: 4–15 seconds.** The script rounds frames to the nearest legal value —
  H3 requires `length ≡ 5 (mod 17)`, e.g. 2s=56, 4s=107, 8s=192. Don't hand-pick frames.
- **No timeline control.** The model reads the prompt as one description; it does not
  honor "from 0:00 to 0:04 … then from 0:04 to 0:08". For distinct beats, render
  separate clips and cut them together.
- **Dialogue languages:** Arabic, Chinese, English, French, German, Italian, Japanese,
  Korean, Portuguese, Russian, Spanish.
- **Prompt for the audio too.** Describing what is heard ("a man's voiceover explains…",
  "leaves crunch underfoot") is what drives the soundtrack.

## Writing good H3 prompts

One flowing description, not a shot list. Cover: subject and action, camera framing and
movement, lighting and film look, and the audio. Concrete beats vague — "vintage claw
hammer on dark weathered wood, natural workshop lighting, shallow depth of field" over
"a nice tool video".

## If there's no egress

If the sandbox cannot reach `*.proxy.runpod.net`, the push approach in this skill will
not work at all. Do not fake it or retry in a loop. Report it plainly, and offer the
pull design instead: a poll loop running inside the pod that watches a job file on the
network volume and renders whatever appears there, driven via JupyterLab on port 8888.
That requires a one-time setup and the user's go-ahead.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `403` on any request | RunPod proxy rejects the `Python-urllib` User-Agent. The script already sets `curl/8.7.1`; anything hand-rolled must too. |
| `502` on port 8188 | ComfyUI not up yet. Wait. |
| `CERTIFICATE_VERIFY_FAILED` | Python without a CA store. The script sets `SSL_CERT_FILE` automatically; otherwise export it. |
| Empty loader dropdowns / missing models | Pod booted without `download_minimax_h3=true`. |
| `OCI runtime create failed` | Host CUDA is older than the image needs. Redeploy with min CUDA 13.0, or use the `-cuda12` image tag. |
