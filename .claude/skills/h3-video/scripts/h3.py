#!/usr/bin/env python3
"""Drive MiniMax-H3 (ComfyUI) on a RunPod pod over its HTTP API.

Subcommands:
  status  POD_ID              -- is ComfyUI answering yet?
  wait    POD_ID              -- block until ComfyUI is ready
  run     POD_ID --prompt ... -- render a clip and download it

Notes that cost real debugging time:
  * RunPod's proxy returns 403 for the default Python-urllib User-Agent.
  * H3 frame counts must be congruent to 5 mod 17.
"""
import argparse, json, os, random, ssl, sys, time
import urllib.error, urllib.parse, urllib.request

UA = "curl/8.7.1"  # anything but Python-urllib; the proxy 403s that
FPS = 24

# python.org builds on macOS ship without a usable CA store.
for _c in ("/etc/ssl/cert.pem", "/etc/pki/tls/certs/ca-bundle.crt"):
    if not os.environ.get("SSL_CERT_FILE") and os.path.exists(_c):
        os.environ["SSL_CERT_FILE"] = _c
        break


def base(pod):
    return f"https://{pod}-8188.proxy.runpod.net"


def _req(url, data=None, timeout=60):
    h = {"User-Agent": UA}
    if data is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    return urllib.request.Request(url, data=data, headers=h,
                                  method="POST" if data else "GET")


def get(pod, path, timeout=60):
    with urllib.request.urlopen(_req(base(pod) + path), timeout=timeout) as r:
        return json.load(r)


def post(pod, path, payload, timeout=60):
    with urllib.request.urlopen(_req(base(pod) + path, payload), timeout=timeout) as r:
        return json.load(r)


def download(pod, path, dest, timeout=600):
    with urllib.request.urlopen(_req(base(pod) + path), timeout=timeout) as r, \
            open(dest, "wb") as f:
        f.write(r.read())


def frames_for(seconds, fps=FPS):
    """H3 requires length == 5 (mod 17). Mirrors the template's math node."""
    n = max(5, round(seconds * fps))
    return n + (5 - (n % 17)) % 17


def graph(prompt, width, height, length, steps, seed, fps=FPS,
          prefix="video/MiniMax_H3", image_name=None):
    g = {
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
        "17": {"class_type": "KSamplerSelect", "inputs": {
            "sampler_name": "res_multistep"}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["15", 0], "guider": ["16", 0], "sampler": ["17", 0],
            "sigmas": ["9", 0], "latent_image": ["104", 1]}},
        "10": {"class_type": "VAEDecode", "inputs": {
            "samples": ["14", 0], "vae": ["11", 0]}},
        "23": {"class_type": "VAEDecodeAudio", "inputs": {
            "samples": ["14", 0], "vae": ["24", 0]}},
        "91": {"class_type": "CreateVideo", "inputs": {
            "images": ["10", 0], "fps": float(fps),
            "audio": ["23", 0], "bit_depth": 8}},
        "92": {"class_type": "SaveVideo", "inputs": {
            "video": ["91", 0], "filename_prefix": prefix,
            "format": "auto", "codec": "auto"}},
    }
    if image_name:  # image-to-video: feed the uploaded still as first frame
        g["200"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        g["104"]["inputs"]["first_frame"] = ["200", 0]
    return g


def upload_image(pod, path):
    """Multipart upload to ComfyUI's /upload/image. Returns the stored name."""
    import mimetypes, uuid
    name = os.path.basename(path)
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        open(path, "rb").read(), b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        base(pod) + "/upload/image", data=body, method="POST",
        headers={"User-Agent": UA,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        info = json.load(r)
    return (info.get("subfolder") + "/" if info.get("subfolder") else "") + info["name"]


def cmd_status(a):
    try:
        s = get(a.pod, "/system_stats", timeout=20)
        dev = s["devices"][0]
        print(f"READY  comfyui={s['system']['comfyui_version']}  "
              f"gpu={dev['name'].split(':')[1].strip()}  "
              f"vram_free={dev['vram_free']/2**30:.0f}GiB")
        return 0
    except Exception as e:
        print(f"NOT READY ({type(e).__name__}: {e})")
        return 1


def cmd_wait(a):
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        try:
            get(a.pod, "/system_stats", timeout=20)
            print(f"ready after {time.time()-t0:.0f}s")
            return 0
        except Exception as e:
            print(f"  {time.time()-t0:5.0f}s waiting… ({type(e).__name__})", flush=True)
            time.sleep(a.interval)
    print("TIMEOUT")
    return 1


def cmd_run(a):
    seed = a.seed if a.seed is not None else random.randint(0, 2**32 - 1)
    length = frames_for(a.seconds)
    image_name = upload_image(a.pod, a.image) if a.image else None
    if image_name:
        print("uploaded:", image_name, flush=True)
    g = graph(a.prompt, a.width, a.height, length, a.steps, seed,
              image_name=image_name)
    print(f"seed={seed} frames={length} ({length/FPS:.2f}s) "
          f"{a.width}x{a.height} steps={a.steps}", flush=True)

    r = post(a.pod, "/prompt", {"prompt": g})
    if "prompt_id" not in r:
        print("SUBMIT FAILED:", json.dumps(r)[:2000]); return 1
    pid = r["prompt_id"]
    print("prompt_id:", pid, flush=True)

    t0 = time.time()
    while time.time() - t0 < a.timeout:
        hist = get(a.pod, f"/history/{pid}")
        if pid in hist:
            st = hist[pid].get("status", {})
            if st.get("completed"):
                break
            if st.get("status_str") == "error":
                print("RENDER ERROR:", json.dumps(st)[:3000]); return 1
        q = get(a.pod, "/queue")
        print(f"  t={time.time()-t0:6.0f}s "
              f"running={len(q.get('queue_running',[]))} "
              f"pending={len(q.get('queue_pending',[]))}", flush=True)
        time.sleep(a.interval)
    else:
        print("TIMEOUT waiting for render"); return 1

    os.makedirs(a.out, exist_ok=True)
    saved = []
    for _node, o in hist[pid]["outputs"].items():
        for key in ("videos", "gifs", "images"):
            for f in o.get(key, []) or []:
                qs = urllib.parse.urlencode({
                    "filename": f["filename"],
                    "subfolder": f.get("subfolder", ""),
                    "type": f.get("type", "output")})
                dest = os.path.join(a.out, os.path.basename(f["filename"]))
                download(a.pod, "/view?" + qs, dest)
                saved.append(dest)
                print(f"saved: {dest} ({os.path.getsize(dest)/1e6:.1f} MB)", flush=True)
    if not saved:
        print("NO OUTPUT FILES"); return 1
    print(f"done in {time.time()-t0:.0f}s")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status"); s.add_argument("pod"); s.set_defaults(fn=cmd_status)

    w = sub.add_parser("wait"); w.add_argument("pod")
    w.add_argument("--timeout", type=int, default=1800)
    w.add_argument("--interval", type=int, default=15)
    w.set_defaults(fn=cmd_wait)

    r = sub.add_parser("run"); r.add_argument("pod")
    r.add_argument("--prompt", required=True)
    r.add_argument("--out", default="out")
    r.add_argument("--seconds", type=float, default=4.0)
    r.add_argument("--width", type=int, default=1344)
    r.add_argument("--height", type=int, default=768)
    r.add_argument("--steps", type=int, default=20)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--image", default=None, help="still for image-to-video")
    r.add_argument("--timeout", type=int, default=3600)
    r.add_argument("--interval", type=int, default=15)
    r.set_defaults(fn=cmd_run)

    a = p.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
