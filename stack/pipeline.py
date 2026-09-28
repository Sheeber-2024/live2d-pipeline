#!/usr/bin/env python3
"""Live2D 一键流水线：单张插画 → 分层 PSD → Live2D 模型。

编排两个上游开源工程：
  1. see-through（shitagaki-lab）单图图层拆分 —— 本机 M1/8GB 无法本地推理
     （权重约 12GB 且硬编码 CUDA），改走官方 ZeroGPU Space 的 Gradio API。
  2. psd2live（tsunehimatoi）自动绑定 —— 用其自带 JRE 的原生启动器跑 CLI。

用法:
    python3 pipeline.py <插画> [-o 输出目录] [--resolution 1280] [--seed 42] [--tblr-split]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)  # live2d/
# jpackage 生成的产物目录名随平台而异：
#   macOS   -> psd2live.app/Contents/MacOS/PSD2Live   （.app 包）
#   Windows -> PSD2Live/PSD2Live.exe
#   Linux   -> PSD2Live/bin/PSD2Live
_APP_DIR = os.path.join(WORKSPACE, "psd2live", "build", "compose", "binaries", "main", "app")
DEFAULT_PSD2LIVE_APP = os.environ.get(
    "PSD2LIVE_APP",
    os.path.join(_APP_DIR, "psd2live.app") if sys.platform == "darwin" else os.path.join(_APP_DIR, "PSD2Live"),
)


def _java_candidates() -> list:
    """按优先级列出可用的 java 可执行文件。"""
    out = []
    if os.environ.get("PSD2LIVE_JAVA"):
        out.append(os.environ["PSD2LIVE_JAVA"])
    if os.environ.get("JAVA_HOME"):
        out.append(os.path.join(os.environ["JAVA_HOME"], "bin",
                                "java.exe" if os.name == "nt" else "java"))
    found = shutil.which("java")
    if found:
        out.append(found)
    jh_file = os.path.join(HERE, "tools", "java_home.txt")
    if os.path.isfile(jh_file):
        h = open(jh_file).read().strip()
        if h:
            out.append(os.path.join(h, "bin", "java.exe" if os.name == "nt" else "java"))
    return [p for p in out if p and os.path.isfile(p)]

sys.path.insert(0, HERE)
import seethrough_client  # noqa: E402


def find_psd2live() -> tuple[str, list[str]]:
    """定位 psd2live 启动方式，返回 (可执行文件, 前置参数)。

    优先级：
      1. jpackage 产出的原生启动器（自带 JRE，零外部依赖）—— 各平台路径不同
      2. app 目录里的 jar + 系统/自带 java（java -cp）
    """
    app = os.environ.get("PSD2LIVE_APP", DEFAULT_PSD2LIVE_APP)
    n = os.name == "nt"

    # --- 1. 原生启动器 ---
    native_candidates = [
        os.path.join(app, "Contents", "MacOS", "PSD2Live"),          # macOS
        os.path.join(app, "bin", "PSD2Live"),                        # Linux
        os.path.join(app, "PSD2Live.exe"),                           # Windows
        os.path.join(app, "PSD2Live"),                               # Windows（无扩展名兜底）
        os.path.join(app, "bin", "PSD2Live.bat"),
    ]
    # 直接用 exe 启动时，若旁边有 runtime，交给它即可（jpackage 已处理好）
    for cand in native_candidates:
        if os.path.isfile(cand) and (n or os.access(cand, os.X_OK)):
            return cand, []

    # --- 2. jar + java ---
    jar_dirs = [
        os.path.join(app, "Contents", "app"),   # macOS
        os.path.join(app, "app"),               # Windows
        os.path.join(app, "lib", "app"),        # Linux
    ]
    for jar_dir in jar_dirs:
        if not os.path.isdir(jar_dir):
            continue
        has_jar = any(f.endswith(".jar") for f in os.listdir(jar_dir))
        if not has_jar:
            continue
        # 优先用产物自带的 runtime（无需系统装 Java）
        bundled = []
        if n:
            bundled = [os.path.join(app, "runtime", "bin", "java.exe")]
        else:
            bundled = [os.path.join(app, "runtime", "bin", "java"),
                       os.path.join(app, "runtime", "Contents", "Home", "bin", "java")]
        javas = [j for j in bundled if os.path.isfile(j)] + _java_candidates()
        if javas:
            cp = os.path.join(jar_dir, "*")
            return javas[0], ["-cp", cp, "io.github.psd2live.MainKt"]

    raise RuntimeError(
        "找不到 psd2live。请先构建：\n"
        + ("  cd psd2live && gradlew.bat createDistributable\n" if n
           else "  cd psd2live && ./gradlew createDistributable\n")
        + f"（期望的启动器位于 {app}）\n"
        + "  或用环境变量指定：PSD2LIVE_APP=<产物目录>  PSD2LIVE_JAVA=<java 路径>"
    )


def rig(psd_path: str, out_dir: str, extra_args: list[str] | None = None,
        on_log=None) -> dict:
    """调 psd2live 把分层 PSD 自动绑定成 Live2D 模型。"""
    exe, prefix = find_psd2live()
    os.makedirs(out_dir, exist_ok=True)
    cmd = [exe, *prefix, "--input", psd_path, "--output", out_dir, "--lang", "zh"]
    cmd += extra_args or []
    if on_log:
        on_log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if on_log and proc.stdout:
        for line in proc.stdout.strip().splitlines():
            on_log(line)
    if proc.returncode != 0:
        raise RuntimeError(
            f"psd2live 失败（退出码 {proc.returncode}）\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    files = sorted(
        os.path.join(dp, f)
        for dp, _, fs in os.walk(out_dir)
        for f in fs
    )
    moc3 = [f for f in files if f.endswith(".moc3")]
    if not moc3:
        raise RuntimeError(f"psd2live 未产出 .moc3（输出目录：{out_dir}）")
    return {"files": files, "moc3": moc3[0], "out_dir": out_dir, "stdout": proc.stdout}


def package(model_dir: str, zip_path: str) -> str:
    """把模型文件族打成 zip，便于下载/导入 VTube Studio。"""
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(model_dir):
            for f in fs:
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, model_dir))
    return zip_path


def run_from_psd(psd_path: str, out_root: str, rig_args: list[str] | None = None,
                 on_progress=None, on_log=None, name: str | None = None,
                 span: tuple[float, float] = (0.0, 1.0)) -> dict:
    """已有分层 PSD → Live2D 模型（跳过在线拆层，不消耗任何在线额度）。"""
    lo, hi = span

    def prog(stage: str, frac: float, msg: str = ""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "message": msg})

    def log(msg: str):
        if on_log:
            on_log(msg)

    if not os.path.isfile(psd_path):
        raise FileNotFoundError(psd_path)
    name = name or os.path.splitext(os.path.basename(psd_path))[0]
    out_root = os.path.abspath(out_root)
    model_dir = os.path.join(out_root, f"{name}-live2d")

    t0 = time.time()
    prog("rigging", lo + (hi - lo) * 0.06, "自动绑定中（本地）")
    rig_res = rig(psd_path, model_dir, extra_args=rig_args, on_log=log)
    log(f"绑定完成：{rig_res['moc3']}")
    prog("rigging", lo + (hi - lo) * 0.92, f"已生成 {os.path.basename(rig_res['moc3'])}")

    zip_path = package(model_dir, os.path.join(out_root, f"{name}-live2d.zip"))
    prog("done", hi, "完成")
    return {
        "name": name,
        "image": None,
        "psd": os.path.abspath(psd_path),
        "layer_dir": os.path.dirname(os.path.abspath(psd_path)),
        "model_dir": model_dir,
        "moc3": rig_res["moc3"],
        "files": rig_res["files"],
        "zip": zip_path,
        "seconds": round(time.time() - t0, 1),
    }


def run_pipeline(image: str, out_root: str, resolution: float = 1280, seed: float = 42,
                 tblr_split: bool = False, rig_args: list[str] | None = None,
                 on_progress=None, on_log=None) -> dict:
    """完整流水线：插画 → see-through → PSD → psd2live → Live2D 模型。"""
    def prog(stage: str, frac: float, msg: str = ""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "message": msg})

    def log(msg: str):
        if on_log:
            on_log(msg)

    if not os.path.isfile(image):
        raise FileNotFoundError(image)

    name = os.path.splitext(os.path.basename(image))[0]
    out_root = os.path.abspath(out_root)
    layer_dir = os.path.join(out_root, f"{name}-layers")
    model_dir = os.path.join(out_root, f"{name}-live2d")
    os.makedirs(layer_dir, exist_ok=True)

    t0 = time.time()
    prog("layer-split", 0.02, "上传并拆分图层（在线，约 2-4 分钟）")
    res = seethrough_client.run(
        image, layer_dir, resolution=resolution, seed=seed,
        tblr_split=tblr_split, quiet=True,
        on_log=log,
    )
    psds = [f for f in res["files"] if f.lower().endswith((".psd", ".psb"))]
    if not psds:
        # Space 可能回 zip，解出来找 PSD
        for f in list(res["files"]):
            if f.lower().endswith(".zip"):
                with zipfile.ZipFile(f) as z:
                    z.extractall(layer_dir)
                psds += [
                    os.path.join(layer_dir, n) for n in z.namelist()
                    if n.lower().endswith((".psd", ".psb"))
                ]
    if not psds:
        raise RuntimeError(f"see-through 未产出 PSD，实际产物：{res['files']}")
    psd = psds[0]
    log(f"图层拆分完成：{psd}")
    prog("layer-split", 0.62, f"已拆出 {os.path.basename(psd)}")

    prog("rigging", 0.64, "自动绑定中（本地）")
    res = run_from_psd(psd, out_root, rig_args=rig_args, on_progress=on_progress,
                       on_log=on_log, name=name, span=(0.64, 1.0))
    res["image"] = os.path.abspath(image)
    res["layer_dir"] = layer_dir
    res["seconds"] = round(time.time() - t0, 1)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="插画 → Live2D 一键流水线")
    ap.add_argument("image", help="输入插画")
    ap.add_argument("-o", "--output", default=None, help="输出目录（默认 ./output/<名字>）")
    ap.add_argument("--resolution", type=float, default=1280, help="拆分分辨率（默认 1280）")
    ap.add_argument("--seed", type=float, default=42)
    ap.add_argument("--tblr-split", action="store_true", help="左右部件分离")
    a = ap.parse_args()

    out_root = a.output or os.path.join(HERE, "output")
    try:
        res = run_pipeline(
            a.image, out_root, a.resolution, a.seed, a.tblr_split,
            on_progress=lambda p: print(f"  [{p['progress']:5.1%}] {p['stage']}: {p['message']}", flush=True),
            on_log=lambda m: print(f"    {m}", flush=True),
        )
    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"\n✅ 完成，用时 {res['seconds']}s")
    print(f"   模型目录: {res['model_dir']}")
    print(f"   运行时模型: {res['moc3']}")
    print(f"   打包: {res['zip']}")
    print(json.dumps({"zip": res["zip"], "moc3": res["moc3"], "model_dir": res["model_dir"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())