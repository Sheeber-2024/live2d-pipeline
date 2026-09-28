#!/usr/bin/env python3
"""See-through 图层拆分客户端，支持多在线后端。

为什么走在线：本机 Apple M1 / 8GB，无法本地推理 see-through——
权重约 12GB（UNet 单文件 8.1GB），且代码硬编码 CUDA（55 处）、零 MPS 支持。

后端（均兼容 Gradio API 协议）：
  hf          HuggingFace 官方 Space。匿名额度极低（每天 1~2 次），配 HF_TOKEN 后大幅提升。
  hf-nf4      社区 4-bit 量化版 Space，API 契约一致，作额外额度池。
  modelscope  ModelScope 官方 API 地址。必须配 MODELSCOPE_TOKEN；国内直连、额度宽松。

Token（均免费）：
  HF:         https://huggingface.co/settings/tokens     export HF_TOKEN=hf_xxx
  ModelScope: https://modelscope.cn/my/myaccesstoken     export MODELSCOPE_TOKEN=ms_xxx

用法:
    python3 seethrough_client.py <图片> <输出目录> [--backend hf-nf4] [--resolution 1280]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid


def _install_ca_bundle() -> None:
    """macOS 上 python.org 版 Python 不自带 CA，导致 HTTPS 校验失败。

    优先 certifi，其次系统证书；找不到就保持默认（不静默降级为不校验）。
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    candidates = []
    try:
        import certifi
        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates += ["/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem"]
    for path in candidates:
        if path and os.path.isfile(path):
            os.environ["SSL_CERT_FILE"] = path
            ctx = ssl.create_default_context(cafile=path)
            ssl._create_default_https_context = lambda *a, **k: ctx  # noqa: SLF001
            return


_install_ca_bundle()

def _which_python() -> str:
    """挑一个可用的 Python 解释器：Windows 上是 python，类 Unix 上通常是 python3。"""
    import shutil
    for name in (("python", "python3") if os.name == "nt" else ("python3", "python")):
        found = shutil.which(name)
        if found:
            return found
    return "python3" if os.name != "nt" else "python"


BACKENDS = {
    "hf": {
        "base": os.environ.get("SEETHROUGH_HF_SPACE", "https://24yearsold-see-through-demo.hf.space"),
        "token_env": "HF_TOKEN",
        "label": "HuggingFace 官方 Space",
        "signup": "https://huggingface.co/settings/tokens",
    },
    "hf-nf4": {
        "base": os.environ.get("SEETHROUGH_NF4_SPACE", "https://yuyuanan-see-through-nf4.hf.space"),
        "token_env": "HF_TOKEN",
        "label": "HuggingFace nf4 量化版",
        "signup": "https://huggingface.co/settings/tokens",
    },
    "modelscope": {
        "base": os.environ.get(
            "SEETHROUGH_MS_SPACE",
            "https://studio-ljsabc-see-through.api-inference.modelscope.net",
        ),
        "token_env": "MODELSCOPE_TOKEN",
        "token_required": True,
        "label": "ModelScope",
        "signup": "https://modelscope.cn/my/myaccesstoken",
    },
    # 本地部署：在你自己的 GPU 机器上直接跑 inference_psd.py，不依赖任何在线服务。
    # 需要 NVIDIA GPU（建议 >=12GB 显存）+ CUDA 版 PyTorch + 约 12GB 模型权重。
    "local": {
        "kind": "local",
        "label": "本地 see-through",
        "token_env": "SEETHROUGH_LOCAL_PYTHON",
        "signup": "",
        "repo": os.environ.get(
            "SEETHROUGH_REPO",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "see-through"),
        ),
        "python": os.environ.get("SEETHROUGH_LOCAL_PYTHON") or _which_python(),
        "timeout": float(os.environ.get("SEETHROUGH_LOCAL_TIMEOUT", "7200")),
    },
}
DEFAULT_BACKEND = os.environ.get("SEETHROUGH_BACKEND", "modelscope")
TIMEOUT = float(os.environ.get("SEETHROUGH_TIMEOUT", "900"))

QUOTA_HINTS = ("quota", "exceeded", "too many requests", "rate limit", "gpu time", "429")


class SeethroughError(RuntimeError):
    pass


def _backend(name: str) -> dict:
    if name not in BACKENDS:
        raise SeethroughError(f"未知后端 {name!r}，可选：{', '.join(BACKENDS)}")
    cfg = dict(BACKENDS[name])
    cfg["name"] = name
    cfg["token"] = os.environ.get(cfg["token_env"], "").strip()
    return cfg


def _headers(cfg: dict, extra: dict | None = None) -> dict:
    h = {"User-Agent": "live2d-stack/1.0"}
    if cfg["token"]:
        h["Authorization"] = f"Bearer {cfg['token']}"
    if extra:
        h.update(extra)
    return h


def _explain(cfg: dict, body: str) -> str:
    """把服务端返回翻译成可操作的中文提示。"""
    low = body.lower()
    other = "modelscope" if cfg["name"] != "modelscope" else "hf"
    if any(k in low for k in QUOTA_HINTS):
        return (
            f"{cfg['label']} 额度已用尽。\n"
            f"  → 配置 token 可大幅提升额度（免费）：{cfg['signup']}\n"
            f"  → export {cfg['token_env']}=<你的token>\n"
            f"  → 或换后端：--backend {other}"
        )
    if "authentication" in low or "401" in body or "unauthorized" in low:
        return (
            f"{cfg['label']} 需要认证。请配置 token（免费）：{cfg['signup']}\n"
            f"  → export {cfg['token_env']}=<你的token>"
        )
    if body.strip() in ("null", ""):
        return (
            f"{cfg['label']} 返回空错误——通常是 ZeroGPU 额度耗尽或排队被取消。\n"
            f"  → 配置 token：{cfg['signup']}\n"
            f"  → 稍后重试，或 --backend {other}"
        )
    return f"{cfg['label']} 服务端错误：{body[:600]}"


def _post(cfg: dict, url: str, data: bytes, content_type: str) -> bytes:
    req = urllib.request.Request(url, data=data, headers=_headers(cfg, {"Content-Type": content_type}),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SeethroughError(_explain(cfg, e.read().decode("utf-8", "replace"))) from None


def upload(cfg: dict, image_path: str) -> str:
    """上传图片，返回服务端路径。"""
    boundary = "----ds" + uuid.uuid4().hex
    name = os.path.basename(image_path)
    with open(image_path, "rb") as f:
        content = f.read()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    raw = _post(cfg, f"{cfg['base']}/gradio_api/upload", body,
                f"multipart/form-data; boundary={boundary}")
    paths = json.loads(raw)
    if not paths:
        raise SeethroughError("上传失败：服务未返回路径")
    return paths[0] if isinstance(paths, list) else paths


def submit(cfg: dict, server_path: str, resolution: float, seed: float, tblr_split: bool) -> str:
    """提交推理任务，返回 event_id。"""
    payload = json.dumps({
        "data": [
            {"path": server_path, "meta": {"_type": "gradio.FileData"}},
            resolution,
            seed,
            tblr_split,
        ]
    }).encode()
    raw = _post(cfg, f"{cfg['base']}/gradio_api/call/inference", payload, "application/json")
    try:
        event_id = json.loads(raw).get("event_id")
    except json.JSONDecodeError:
        raise SeethroughError(_explain(cfg, raw.decode("utf-8", "replace"))) from None
    if not event_id:
        raise SeethroughError(_explain(cfg, raw.decode("utf-8", "replace")))
    return event_id


def stream_result(cfg: dict, event_id: str, on_progress=None) -> list:
    """监听 SSE，返回最终 data 列表。"""
    req = urllib.request.Request(
        f"{cfg['base']}/gradio_api/call/inference/{event_id}", headers=_headers(cfg))
    deadline = time.time() + TIMEOUT
    event = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            for raw_line in r:
                if time.time() > deadline:
                    raise SeethroughError(f"推理超时（{TIMEOUT:.0f}s）")
                line = raw_line.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if event == "error":
                        raise SeethroughError(_explain(cfg, data))
                    if event == "complete":
                        return json.loads(data)
                    if event == "progress" and on_progress:
                        try:
                            on_progress(json.loads(data))
                        except Exception:
                            pass
    except urllib.error.HTTPError as e:
        raise SeethroughError(_explain(cfg, e.read().decode("utf-8", "replace"))) from None
    raise SeethroughError("SSE 流意外结束，未收到 complete 事件")


def download(cfg: dict, remote: dict, out_dir: str) -> str:
    """下载结果文件到本地。"""
    path = remote.get("path") or remote.get("url") or ""
    if not path:
        raise SeethroughError(f"结果中没有文件字段：{remote}")
    url = path if path.startswith("http") else f"{cfg['base']}/gradio_api/file={path}"
    os.makedirs(out_dir, exist_ok=True)
    fname = os.path.basename(path) or "seethrough_output.psd"
    dest = os.path.join(out_dir, fname)
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers(cfg)), timeout=600) as r, \
            open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def run_local(image_path: str, out_dir: str, resolution: float = 1280, seed: float = 42,
              tblr_split: bool = False, cfg: dict | None = None, log=None) -> dict:
    """在本机直接跑 see-through 的 inference_psd.py（需要 NVIDIA GPU）。"""
    import subprocess

    cfg = cfg or _backend("local")
    repo = cfg["repo"]
    script = os.path.join(repo, "inference", "scripts", "inference_psd.py")
    if not os.path.isfile(script):
        raise SeethroughError(
            f"找不到本地 see-through：{script}\n"
            f"  → 把仓库克隆到 {repo}，或设置 SEETHROUGH_REPO=<路径>\n"
            f"  → 详见 docs/LOCAL_SEETHROUGH.md"
        )

    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        cfg["python"], script,
        "--srcp", os.path.abspath(image_path),
        "--save_dir", os.path.abspath(out_dir),
        "--resolution", str(int(resolution)),
        "--seed", str(int(seed)),
        "--save_to_psd",
    ]
    if tblr_split:
        cmd.append("--tblr_split")
    log(f"[1/3] 本地推理: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          timeout=cfg.get("timeout", 7200))
    if log and proc.stdout:
        for line in proc.stdout.strip().splitlines()[-20:]:
            log("      " + line)
    if proc.returncode != 0:
        raise SeethroughError(
            f"本地 see-through 失败（退出码 {proc.returncode}）\n"
            f"{proc.stderr[-1500:] if proc.stderr else proc.stdout[-1500:]}"
        )

    # inference_psd.py 会在 save_dir/<图片名>/ 下产出，PSD 在 optimized 子目录
    found = []
    for dp, _, fs in os.walk(out_dir):
        for f in fs:
            if f.lower().endswith((".psd", ".psb")):
                found.append(os.path.join(dp, f))
    if not found:
        raise SeethroughError(f"本地推理完成但未找到 PSD（输出目录 {out_dir}）")
    log(f"[3/3] 产出: {[os.path.basename(f) for f in found]}")
    return {"files": found, "raw": [], "backend": "local"}


def check_backend(name: str, timeout: float = 30) -> dict:
    """轻量校验某后端是否可用：只探认证与连通性，不消耗 GPU 额度。"""
    cfg = _backend(name)
    if cfg.get("kind") == "local":
        script = os.path.join(cfg["repo"], "inference", "scripts", "inference_psd.py")
        if not os.path.isfile(script):
            return {"backend": name, "label": cfg["label"], "ok": False, "has_token": False,
                    "reason": f"未找到本地仓库：{cfg['repo']}（设置 SEETHROUGH_REPO 或见 docs/LOCAL_SEETHROUGH.md）"}
        try:
            import subprocess
            probe = subprocess.run([cfg["python"], "-c", "import torch;print(torch.cuda.is_available())"],
                                   capture_output=True, text=True, timeout=60)
            cuda_ok = probe.stdout.strip().endswith("True")
            return {"backend": name, "label": cfg["label"], "ok": cuda_ok, "has_token": True,
                    "reason": "本地仓库就绪，CUDA 可用" if cuda_ok
                              else "本地仓库就绪，但 CUDA 不可用（推理会极慢或失败）"}
        except FileNotFoundError:
            return {"backend": name, "label": cfg["label"], "ok": False, "has_token": False,
                    "reason": f"找不到 Python：{cfg['python']}（设置 SEETHROUGH_LOCAL_PYTHON）"}
        except Exception as e:
            return {"backend": name, "label": cfg["label"], "ok": False, "has_token": False,
                    "reason": f"{type(e).__name__}: {e}"}
    if cfg.get("token_required") and not cfg["token"]:
        return {"backend": name, "label": cfg["label"], "ok": False, "has_token": False,
                "reason": f"未配置 {cfg['token_env']}（ModelScope 不支持匿名）"}
    try:
        req = urllib.request.Request(f"{cfg['base']}/gradio_api/info", headers=_headers(cfg))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        eps = list((data.get("named_endpoints") or {}).keys())
        return {"backend": name, "label": cfg["label"], "ok": True,
                "has_token": bool(cfg["token"]), "endpoints": eps,
                "reason": "可用（已认证）" if cfg["token"] else "可用，但匿名额度极低（每天 1~2 次）"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"backend": name, "label": cfg["label"], "ok": False,
                "has_token": bool(cfg["token"]), "reason": _explain(cfg, body)}
    except Exception as e:
        return {"backend": name, "label": cfg["label"], "ok": False,
                "has_token": bool(cfg["token"]), "reason": f"{type(e).__name__}: {e}"}


def check_all_backends() -> list:
    """并发探活所有后端，返回结果列表。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(BACKENDS)) as ex:
        return list(ex.map(check_backend, list(BACKENDS)))


def run(image_path: str, out_dir: str, resolution: float = 1280, seed: float = 42,
        tblr_split: bool = False, quiet: bool = False, on_log=None,
        backend: str | None = None, fallback: bool = True) -> dict:
    """跑一次图层拆分：上传 → 推理 → 下载。返回 {files, raw, backend}。

    fallback=True 时，若首选后端额度耗尽/需要认证，自动按序尝试其余后端。
    """
    def log(msg):
        if on_log:
            on_log(msg)
        if not quiet:
            print(msg, flush=True)

    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)

    order = [backend or DEFAULT_BACKEND]
    if fallback:
        order += [b for b in BACKENDS if b not in order]

    last: Exception | None = None
    for i, name in enumerate(order):
        cfg = _backend(name)
        if cfg.get("token_required") and not cfg["token"]:
            log(f"跳过 {cfg['label']}：未配置 {cfg['token_env']}")
            last = SeethroughError(_explain(cfg, "authentication required"))
            continue
        if cfg.get("kind") == "local":
            try:
                return run_local(image_path, out_dir, resolution, seed, tblr_split, cfg, log)
            except SeethroughError as e:
                last = e
                if i < len(order) - 1:
                    log(f"⚠️ {cfg['label']} 失败，尝试下一个后端：{str(e).splitlines()[0]}")
                    continue
                raise
        try:
            log(f"[1/4] 上传 {os.path.basename(image_path)} → {cfg['label']}"
                f"{'' if cfg['token'] else '（匿名）'} ...")
            server_path = upload(cfg, image_path)
            log(f"      -> {server_path}")
            log(f"[2/4] 提交推理 (resolution={resolution:g}, seed={seed:g}, tblr_split={tblr_split}) ...")
            event_id = submit(cfg, server_path, resolution, seed, tblr_split)
            log(f"      event_id={event_id}")
            log("[3/4] 等待处理（通常 2-4 分钟）...")
            t0 = time.time()
            data = stream_result(cfg, event_id,
                                 on_progress=lambda p: log(f"      {p.get('title','')} {p.get('progress',0):.0%}"))
            log(f"      完成，用时 {time.time() - t0:.0f}s")
            log("[4/4] 下载结果 ...")
            saved = []
            for item in data:
                if isinstance(item, dict) and (item.get("path") or item.get("url")):
                    saved.append(download(cfg, item, out_dir))
                elif isinstance(item, str) and item.startswith("/tmp/gradio"):
                    saved.append(download(cfg, {"path": item}, out_dir))
            log(f"      -> {saved}")
            return {"files": saved, "raw": data, "backend": name}
        except SeethroughError as e:
            last = e
            if i < len(order) - 1:
                log(f"⚠️ {cfg['label']} 失败，尝试下一个后端：{str(e).splitlines()[0]}")
                continue
            raise
    raise last or SeethroughError("所有后端均不可用")


def main() -> int:
    ap = argparse.ArgumentParser(description="See-through 在线图层拆分")
    ap.add_argument("image", help="输入插画")
    ap.add_argument("out_dir", help="输出目录")
    ap.add_argument("--resolution", type=float, default=1280)
    ap.add_argument("--seed", type=float, default=42)
    ap.add_argument("--tblr-split", action="store_true", help="左右部件分离（手/眼等）")
    ap.add_argument("--backend", default=None, choices=list(BACKENDS), help="首选后端")
    ap.add_argument("--no-fallback", action="store_true", help="不自动切换后端")
    a = ap.parse_args()
    try:
        res = run(a.image, a.out_dir, a.resolution, a.seed, a.tblr_split,
                  backend=a.backend, fallback=not a.no_fallback)
    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"files": res["files"], "backend": res["backend"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
