#!/usr/bin/env python3
"""离线验证 seethrough_client 的 Gradio 协议实现与错误处理。

不联网、不消耗任何在线额度：本地起一个 mock Space，覆盖正常/认证/配额/进度/网络错误。

用法: python3 test_seethrough_client.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seethrough_client as sc  # noqa: E402

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = os.path.join(HERE, "out-test", "seethrough_output.psd")
IMAGE = os.path.join(os.path.dirname(HERE), "see-through", "common", "assets", "test_image.png")

failures: list[str] = []


def ok(m): print(f"  \u2705 {m}")
def bad(m): print(f"  \u274c {m}"); failures.append(m)


def start_mock(mode: str = "ok", token: str = "") -> subprocess.Popen:
    env = dict(os.environ, MOCK_MODE=mode, MOCK_TOKEN=token, MOCK_FIXTURE=FIXTURE)
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "test_mock_space.py"), str(PORT)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            import urllib.request
            urllib.request.urlopen(f"{BASE}/gradio_api/info", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    return p


def stop(p):
    try:
        p.terminate(); p.wait(timeout=3)
    except Exception:
        p.kill()


def call(backend="hf", token: str | None = None, fallback=False):
    """跑一次客户端（指向 mock），返回 (ok, text)。"""
    old_base = sc.BACKENDS[backend]["base"]
    sc.BACKENDS[backend]["base"] = BASE
    old_tok = os.environ.get(sc.BACKENDS[backend]["token_env"])
    if token is not None:
        os.environ[sc.BACKENDS[backend]["token_env"]] = token
    else:
        os.environ.pop(sc.BACKENDS[backend]["token_env"], None)
    try:
        res = sc.run(IMAGE, tempfile.mkdtemp(), backend=backend, fallback=fallback, quiet=True)
        return True, json.dumps([os.path.basename(f) for f in res["files"]])
    except Exception as e:
        return False, str(e)
    finally:
        sc.BACKENDS[backend]["base"] = old_base
        if old_tok is not None:
            os.environ[sc.BACKENDS[backend]["token_env"]] = old_tok
        else:
            os.environ.pop(sc.BACKENDS[backend]["token_env"], None)


def main() -> int:
    if not os.path.isfile(FIXTURE):
        print(f"缺少测试素材 PSD: {FIXTURE}")
        return 1
    if not os.path.isfile(IMAGE):
        print(f"缺少测试插画: {IMAGE}")
        return 1

    print("\u2550\u2550\u2550 1. \u6b63\u5e38\u6d41\u7a0b\uff08\u533f\u540d\uff09\u2550\u2550\u2550")
    p = start_mock("ok")
    good, text = call("hf", token=None)
    (ok if good else bad)(f"上传\u2192\u63d0\u4ea4\u2192SSE\u2192\u4e0b\u8f7d\uff1a{text}" if good else f"应成功却失败：{text}")
    stop(p)

    print("\u2550\u2550\u2550 2. \u9700\u8981 token \u4f46\u672a\u63d0\u4f9b \u2550\u2550\u2550")
    p = start_mock("ok", token="SECRET")
    good, text = call("hf", token=None)
    if good:
        bad("无 token 却成功了")
    elif "需要认证" in text:
        ok("给出认证提示：" + text.splitlines()[0][:70])
    else:
        bad("提示不准确：" + text.splitlines()[0][:70])
    stop(p)

    print("\u2550\u2550\u2550 3. \u63d0\u4f9b\u6b63\u786e token \u2550\u2550\u2550")
    p = start_mock("ok", token="SECRET")
    good, text = call("hf", token="SECRET")
    (ok if good else bad)(f"带 token 成功：{text}" if good else f"应成功却失败：{text}")
    stop(p)

    print("\u2550\u2550\u2550 4. \u989d\u5ea6\u8017\u5c3d \u2550\u2550\u2550")
    p = start_mock("quota")
    good, text = call("hf", token=None)
    if good:
        bad("配额耗尽却成功了")
    elif "额度" in text:
        ok("识别为额度问题并给出解决路径")
    else:
        bad("提示不准确：" + text.splitlines()[0][:70])
    stop(p)

    print("\u2550\u2550\u2550 5. SSE \u8fdb\u5ea6\u4e8b\u4ef6 \u2550\u2550\u2550")
    p = start_mock("sse_progress")
    seen = []
    old_base = sc.BACKENDS["hf"]["base"]
    sc.BACKENDS["hf"]["base"] = BASE
    try:
        sc.run(IMAGE, tempfile.mkdtemp(), backend="hf", fallback=False,
               quiet=True, on_log=lambda m: seen.append(m))
        (ok if any("50%" in s for s in seen) else bad)("进度事件被解析并上报" if any("50%" in s for s in seen)
                                                      else "未看到进度输出")
    except Exception as e:
        bad(f"进度场景失败：{e}")
    finally:
        sc.BACKENDS["hf"]["base"] = old_base
    stop(p)

    print("\u2550\u2550\u2550 6. \u65e0 token \u7684\u540e\u7aef\u88ab\u8df3\u8fc7 \u2550\u2550\u2550")
    good, text = call("modelscope", token=None)
    if good:
        bad("ModelScope 无 token 却成功")
    elif "MODELSCOPE_TOKEN" in text:
        ok("明确说明缺少哪个 token")
    else:
        bad("提示不准确：" + text.splitlines()[0][:70])

    print("\u2550\u2550\u2550 7. \u540e\u7aef\u4e0d\u53ef\u8fbe \u2550\u2550\u2550")
    old_base = sc.BACKENDS["hf"]["base"]
    sc.BACKENDS["hf"]["base"] = "http://127.0.0.1:1"
    try:
        sc.run(IMAGE, tempfile.mkdtemp(), backend="hf", fallback=False, quiet=True)
        bad("不可达却成功了")
    except Exception as e:
        ok("正确报网络错误：" + str(e).splitlines()[0][:60])
    finally:
        sc.BACKENDS["hf"]["base"] = old_base

    print()
    if failures:
        print(f"\u274c {len(failures)} 项未通过")
        return 1
    print("\u2705 \u5ba2\u6237\u7aef\u534f\u8bae\u4e0e\u9519\u8bef\u5904\u7406\u5168\u90e8\u9a8c\u8bc1\u901a\u8fc7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
