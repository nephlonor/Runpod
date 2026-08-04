#!/usr/bin/env python3
"""Drive MiniMax-H3 t2v on a RunPod ComfyUI pod via its HTTP API."""
import json, sys, time, urllib.request, urllib.parse, random, os

POD = os.environ.get("POD_ID", "rqdp90nd5ke7yz")
BASE = f"https://{POD}-8188.proxy.runpod.net"


def frames_for(seconds, fps=24):
    """H3 needs length congruent to 5 mod 17 (mirrors node 107's expression)."""
    n = max(5, round(seconds * fps))
    return n + (5 - (n % 17)) % 17


def build(prompt, width=864, height=480, seconds=2.0, steps=20, seed=None,
          fps=24, prefix="video/MiniMax_H3"):
    seed = random.randint(0, 2**32 - 1) if seed is None else seed
    length = frames_for(seconds, fps)
    return seed, length, {
        "6":  {"class_type": "UNETLoader", "inputs": {
                 "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                 "weight_dtype": "default"}},
        "13": {"class_type": "CLIPLoader", "inputs": {
                 "clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                 "type": "minimax", "device": "default"}},
        "11": {"class_type": "VAELoader", "inputs": {
                 "vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "24": {"class_type": "VAELoader", "inputs": {
                 "vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
                 "clip": ["13", 0], "vae": ["11", 0], "prompt": prompt,
                 "width": width, "height": height, "length": length}},
        "9":  {"class_type": "BasicScheduler", "inputs": {
                 "model": ["6", 0], "scheduler": "simple",
                 "steps": steps, "denoise": 1.0}},
        "16": {"class_type": "BasicGuider", "inputs": {
                 "model": ["6", 0], "conditioning": ["104", 0]}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "17": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
                 "noise": ["15", 0], "guider": ["16", 0], "sampler": ["17", 0],
                 "sigmas": ["9", 0], "latent_image": ["104", 1]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["11", 0]}},
        "23": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["24", 0]}},
        "91": {"class_type": "CreateVideo", "inputs": {
                 "images": ["10", 0], "fps": float(fps), "audio": ["23", 0], "bit_depth": 8}},
        "92": {"class_type": "SaveVideo", "inputs": {
                 "video": ["91", 0], "filename_prefix": prefix,
                 "format": "auto", "codec": "auto"}},
    }


# RunPod's proxy rejects the default Python-urllib user-agent with a 403.
UA = "curl/8.7.1"


def post(path, payload):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch(path, dest):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        f.write(r.read())


def run(prompt, out_dir, **kw):
    seed, length, graph = build(prompt, **kw)
    print(f"seed={seed} frames={length} ({length/24:.2f}s @24fps)", flush=True)
    r = post("/prompt", {"prompt": graph})
    pid = r["prompt_id"]
    print("prompt_id:", pid, flush=True)

    t0 = time.time()
    while True:
        h = get(f"/history/{pid}")
        if pid in h:
            entry = h[pid]
            st = entry.get("status", {})
            if st.get("status_str") == "error" or st.get("completed") is False and "error" in json.dumps(st):
                print("ERROR:", json.dumps(st)[:3000]); return None
            if st.get("completed"):
                break
        q = get("/queue")
        running = len(q.get("queue_running", []))
        pend = len(q.get("queue_pending", []))
        print(f"  t={time.time()-t0:6.0f}s running={running} pending={pend}", flush=True)
        if running == 0 and pend == 0:
            h = get(f"/history/{pid}")
            if pid in h and h[pid].get("status", {}).get("completed"):
                break
            if pid in h:
                print("finished-but-not-completed:", json.dumps(h[pid].get("status"))[:2000]); return None
        time.sleep(10)

    outs = h[pid]["outputs"]
    print("outputs:", json.dumps(outs)[:1500], flush=True)
    saved = []
    for node, o in outs.items():
        for key in ("videos", "gifs", "images"):
            for f in o.get(key, []) or []:
                qs = urllib.parse.urlencode({"filename": f["filename"],
                                             "subfolder": f.get("subfolder", ""),
                                             "type": f.get("type", "output")})
                dest = os.path.join(out_dir, os.path.basename(f["filename"]))
                fetch("/view?" + qs, dest)
                saved.append(dest)
                print("saved:", dest, os.path.getsize(dest), "bytes", flush=True)
    print(f"total {time.time()-t0:.0f}s")
    return saved


if __name__ == "__main__":
    p = sys.argv[1]
    d = sys.argv[2] if len(sys.argv) > 2 else "."
    kw = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    run(p, d, **kw)
