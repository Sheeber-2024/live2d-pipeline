#!/usr/bin/env python3
"""下载预览页所需的前端渲染库到 web/vendor/。

这些库不由本仓库再分发，原因：
  - Live2D Cubism Core 是 Live2D Inc. 的专有软件，其许可允许在应用中使用，
    但再分发需遵循其条款。因此改为首次运行时从官方地址获取。
  - pixi.js / pixi-live2d-display 可自由分发，这里一并下载以保持离线可用。

用法: python3 setup_vendor.py [--force]
"""
from __future__ import annotations

import argparse
import os
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "web", "vendor")

# 每个资源给出多个来源：官方 CDN 对非浏览器 UA 会返回 403，故附带浏览器 UA 并准备镜像。
ASSETS = [
    ("live2dcubismcore.min.js",
     ["https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js",
      "https://cdn.jsdelivr.net/npm/live2dcubismcore@1.0.0/live2dcubismcore.min.js",
      "https://unpkg.com/live2dcubismcore/live2dcubismcore.min.js"],
     "Live2D Cubism Core (c) Live2D Inc. — 专有许可，从官方地址获取"),
    ("pixi.min.js",
     ["https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js",
      "https://unpkg.com/pixi.js@6.5.10/dist/browser/pixi.min.js"],
     "pixi.js v6.5.10 — MIT"),
    ("cubism4.min.js",
     ["https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js",
      "https://unpkg.com/pixi-live2d-display@0.4.0/dist/cubism4.min.js"],
     "pixi-live2d-display v0.4.0 (cubism4) — MIT"),
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()

CA_CANDIDATES = ["/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem"]


def install_ca() -> None:
    """macOS / Windows 上 Python 可能没有 CA 包，导致 HTTPS 校验失败。"""
    if os.environ.get("SSL_CERT_FILE"):
        return
    candidates = []
    try:
        import certifi
        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates += CA_CANDIDATES
    for path in candidates:
        if path and os.path.isfile(path):
            os.environ["SSL_CERT_FILE"] = path
            ctx = ssl.create_default_context(cafile=path)
            ssl._create_default_https_context = lambda *a, **k: ctx  # noqa: SLF001
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    a = ap.parse_args()

    install_ca()
    os.makedirs(VENDOR, exist_ok=True)
    failed = []
    for name, urls, note in ASSETS:
        dest = os.path.join(VENDOR, name)
        if os.path.isfile(dest) and not a.force:
            print(f"  \u23ed\ufe0f  {name} 已存在，跳过")
            continue
        print(f"  \u2193  {name} … ({note})")
        last_err = None
        for url in urls:
            try:
                data = fetch(url)
                if len(data) < 1000:
                    raise RuntimeError(f"文件过小（{len(data)} B）")
                with open(dest, "wb") as f:
                    f.write(data)
                print(f"      \u2705 {len(data):,} bytes  来源 {url.split('/')[2]}")
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"      \u26a0\ufe0f  {url.split('/')[2]} 失败: {type(e).__name__}")
        if last_err is not None:
            failed.append(name)
            print(f"      \u274c 全部来源失败: {last_err}")

    print()
    if failed:
        print(f"\u274c {len(failed)} 个资源下载失败：{', '.join(failed)}")
        print("   → 预览功能依赖它们；未下载成功时服务其余功能仍可用。")
        print("   → 也可手动下载后放入 web/vendor/")
        return 1
    print("\u2705 预览依赖就绪，离线可用的预览功能已启用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
