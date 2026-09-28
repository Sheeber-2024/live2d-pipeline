#!/usr/bin/env python3
"""流水线自检：验证编排逻辑与产物完整性，不消耗在线额度。

用法: python3 selfcheck.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline            # noqa: E402
import seethrough_client   # noqa: E402

WORKSPACE = os.path.dirname(HERE)


def find_fixture_psd():
    """找一个已有的分层 PSD 作为 mock 拆层结果。"""
    direct = os.path.join(HERE, "out-test", "seethrough_output.psd")
    if os.path.isfile(direct):
        return direct
    for root in (os.path.join(HERE, "jobs"), os.path.join(HERE, "out-test")):
        if os.path.isdir(root):
            for dp, _, fs in os.walk(root):
                for f in fs:
                    if f.lower().endswith((".psd", ".psb")):
                        return os.path.join(dp, f)
    return None


def find_fixture_image():
    d = os.path.join(WORKSPACE, "see-through", "common", "assets")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".png"):
                return os.path.join(d, f)
    return None


def check_artifacts(model_dir):
    """验证产物：moc3 magic、model3.json 引用完整性、JSON 有效性。"""
    problems = []
    m3 = [f for f in os.listdir(model_dir) if f.endswith(".model3.json")]
    if not m3:
        return ["未找到 *.model3.json"]
    spec = json.load(open(os.path.join(model_dir, m3[0])))
    refs = spec["FileReferences"]

    moc = os.path.join(model_dir, refs["Moc"])
    if not os.path.isfile(moc):
        problems.append(f"moc3 缺失: {refs['Moc']}")
    elif open(moc, "rb").read(4) != b"MOC3":
        problems.append(f"moc3 magic 异常: {open(moc, 'rb').read(4)!r}")

    def chk(rel):
        p = os.path.join(model_dir, rel)
        if not os.path.isfile(p):
            problems.append(f"引用缺失: {rel}")
        elif rel.endswith(".json"):
            try:
                json.load(open(p))
            except Exception as e:
                problems.append(f"JSON 无效 {rel}: {e}")

    for t in refs.get("Textures", []):
        chk(t)
    for k in ("Physics", "DisplayInfo", "Pose", "UserData"):
        if refs.get(k):
            chk(refs[k])
    for _, motions in (refs.get("Motions") or {}).items():
        for mo in motions:
            chk(mo["File"])
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时输出目录")
    a = ap.parse_args()

    failures = []

    def ok(msg):
        print(f"  \u2705 {msg}")

    def warn(msg):
        print(f"  \u26a0\ufe0f  {msg}")

    def bad(msg):
        print(f"  \u274c {msg}")
        failures.append(msg)

    print("\u2550\u2550\u2550 1. psd2live \u53ef\u7528\u6027 \u2550\u2550\u2550")
    try:
        exe, prefix = pipeline.find_psd2live()
        ok(f"psd2live: {os.path.basename(exe)} {' '.join(prefix)}".strip())
    except Exception as e:
        bad(f"psd2live 不可用: {e}")
        return 1

    print("\u2550\u2550\u2550 2. \u5728\u7ebf\u540e\u7aef\u8fde\u901a\u6027\uff08\u4e0d\u6d88\u8017\u989d\u5ea6\uff09\u2550\u2550\u2550")
    for r in seethrough_client.check_all_backends():
        msg = f"{r['label']}: {r['reason'].splitlines()[0][:70]}"
        if r["ok"]:
            ok(msg)
        elif "\u672a\u914d\u7f6e" in r["reason"]:
            warn(msg + "  <- 环境未配置，不计为失败")
        elif r["backend"] == "local":
            # 本地后端是可选能力（需自备 GPU 机器），本机没装不算失败
            warn(msg + "  <- 可选：需自备 GPU 机器，见 docs/LOCAL_SEETHROUGH.md")
        else:
            bad(msg)

    print("\u2550\u2550\u2550 3. \u7f16\u6392\u903b\u8f91\uff08mock \u62c6\u5c42 + \u771f\u5b9e\u7ed1\u5b9a\uff09\u2550\u2550\u2550")
    psd_fixture = find_fixture_psd()
    img_fixture = find_fixture_image()
    if not psd_fixture or not img_fixture:
        bad(f"缺少测试素材 (psd={psd_fixture}, img={img_fixture})")
    else:
        tmp = tempfile.mkdtemp(prefix="live2d-selfcheck-")
        real_run = seethrough_client.run

        def fake_run(image_path, out_dir, **kw):
            """模拟在线拆层：把已有的真实 PSD 放进输出目录。"""
            os.makedirs(out_dir, exist_ok=True)
            dest = os.path.join(out_dir, "seethrough_output.psd")
            shutil.copy(psd_fixture, dest)
            return {"files": [dest], "raw": [], "backend": "mock"}

        seethrough_client.run = fake_run
        try:
            events = []
            res = pipeline.run_pipeline(img_fixture, tmp,
                                        on_progress=lambda p: events.append(p["stage"]),
                                        on_log=lambda m: None)
            ok(f"编排完成，用时 {res['seconds']}s")
            if {"layer-split", "rigging", "done"} <= set(events):
                ok(f"阶段回调完整: {events}")
            else:
                bad(f"阶段回调缺失: {events}")
            ok(f"打包: {os.path.basename(res['zip'])}") if os.path.isfile(res["zip"]) else bad("zip 未生成")
            probs = check_artifacts(res["model_dir"])
            if probs:
                for p in probs:
                    bad(p)
            else:
                ok(f"产物验收通过（moc3 magic + 引用 + JSON，{len(res['files'])} 个文件）")
        except Exception as e:
            bad(f"编排失败: {type(e).__name__}: {e}")
        finally:
            seethrough_client.run = real_run
            if a.keep:
                print(f"     临时目录保留: {tmp}")
            else:
                shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"\u274c 自检未通过（{len(failures)} 项）")
        return 1
    print("\u2705 全部自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
