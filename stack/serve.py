#!/usr/bin/env python3
"""Live2D 一站式服务：上传插画 → 自动拆层 → 自动绑定 → 下载 Live2D 模型。

纯 Python 标准库实现，无第三方依赖。
    python3 serve.py [--port 8770] [--open]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
import pipeline  # noqa: E402

JOBS_DIR = os.path.join(HERE, "jobs")
MAX_UPLOAD = 64 * 1024 * 1024

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- 任务管理

def _new_job(name: str, params: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "name": name,
            "params": params,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "message": "排队中",
            "logs": [],
            "result": None,
            "error": None,
            "created": time.time(),
        }
    return job_id


def _update(job_id: str, **fields):
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields)


def _append_log(job_id: str, line: str):
    with _lock:
        job = _jobs.get(job_id)
        if job is not None and len(job["logs"]) < 500:
            job["logs"].append(f"[{time.strftime('%H:%M:%S')}] {line}")


def _unpack_zip(zip_path: str, dest: str) -> str | None:
    """从 zip 里找第一个 PSD/PSB，解出来并返回路径。"""
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith((".psd", ".psb")) and not n.startswith("__MACOSX")]
            if not names:
                return None
            # 选体积最大的那个（避免选到缩略图）
            target = max(names, key=lambda n: z.getinfo(n).file_size)
            z.extract(target, dest)
            return os.path.join(dest, target)
    except Exception:
        return None


def _run_job(job_id: str, image_path: str, out_dir: str, params: dict):
    # 上传的是 zip：尝试解出里面的 PSD
    if image_path.lower().endswith(".zip"):
        extracted = _unpack_zip(image_path, os.path.join(out_dir, "unpacked"))
        if extracted:
            _append_log(job_id, f"从 zip 解出 {os.path.basename(extracted)}")
            image_path = extracted
        else:
            _update(job_id, status="error", error="zip 里没有找到 PSD/PSB 文件")
            _append_log(job_id, "❌ zip 内未找到 PSD")
            return

    is_psd = image_path.lower().endswith((".psd", ".psb"))
    _update(job_id, status="running",
            stage="rigging" if is_psd else "layer-split",
            progress=0.01, message="开始")
    try:
        def on_progress(p):
            _update(job_id, stage=p["stage"], progress=p["progress"], message=p.get("message", ""))

        common = dict(on_progress=on_progress, on_log=lambda m: _append_log(job_id, m))
        if is_psd:
            # 已是分层 PSD：跳过在线拆层，纯本地绑定，不消耗任何额度
            _append_log(job_id, "检测到分层 PSD，跳过在线拆层，直接绑定")
            result = pipeline.run_from_psd(image_path, out_dir, **common)
        else:
            result = pipeline.run_pipeline(
                image_path, out_dir,
                resolution=params.get("resolution", 1280),
                seed=params.get("seed", 42),
                tblr_split=params.get("tblr_split", False),
                **common,
            )
        _update(job_id, status="done", stage="done", progress=1.0,
                message=f"完成，用时 {result['seconds']}s",
                result={k: v for k, v in result.items() if k in
                        ("name", "psd", "model_dir", "moc3", "zip", "seconds", "files")})
    except Exception as e:
        _update(job_id, status="error", error=f"{type(e).__name__}: {e}")
        _append_log(job_id, f"❌ {type(e).__name__}: {e}")
        _append_log(job_id, traceback.format_exc()[-1500:])


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "Live2DStack/1.0"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    # -- helpers --
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    # -- routes --
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")

        if path == "/api/health":
            try:
                pipeline.find_psd2live()
                ok, detail = True, "psd2live 就绪"
            except Exception as e:
                ok, detail = False, str(e)
            sc = pipeline.seethrough_client
            cur = sc._backend(sc.DEFAULT_BACKEND)
            return self._json(200 if ok else 503, {
                "ok": ok,
                "detail": detail,
                "backend": sc.DEFAULT_BACKEND,
                "backend_label": cur["label"],
                "has_token": bool(cur["token"]),
                "backends": list(sc.BACKENDS),
            })

        if path == "/preview":
            fp = os.path.join(HERE, "web", "preview.html")
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            return self._json(404, {"error": "preview.html 缺失"})

        if path.startswith("/vendor/"):
            fname = os.path.basename(path[len("/vendor/"):])
            fp = os.path.join(HERE, "web", "vendor", fname)
            if os.path.isfile(fp):
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), ctype)
            return self._json(404, {"error": f"vendor 资源不存在: {fname}"})

        if path == "/api/backends":
            return self._json(200, {"backends": pipeline.seethrough_client.check_all_backends()})

        if path == "/api/jobs":
            with _lock:
                items = [{k: j[k] for k in ("id", "name", "status", "progress", "message", "created")}
                         for j in _jobs.values()]
            items.sort(key=lambda x: -x["created"])
            return self._json(200, {"jobs": items[:50]})

        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            job_id, _, action = rest.partition("/")
            with _lock:
                job = _jobs.get(job_id)
            if not job:
                return self._json(404, {"error": "任务不存在"})
            if action.startswith("model"):
                return self._serve_model(job, action[len("model"):].lstrip("/"))

            if action == "download":
                return self._download(job)
            if action == "":
                return self._json(200, job)
            return self._json(404, {"error": "未知操作"})

        return self._json(404, {"error": "not found"})

    def _download(self, job: dict):
        if job["status"] != "done" or not job.get("result"):
            return self._json(409, {"error": "任务尚未完成"})
        zip_path = job["result"].get("zip") or ""
        if not os.path.isfile(zip_path):
            return self._json(404, {"error": "模型包不存在"})
        fname = f"{job['name']}-live2d.zip"
        with open(zip_path, "rb") as f:
            data = f.read()
        self._send(200, data, "application/zip",
                   {"Content-Disposition": f'attachment; filename="{fname}"'})


    def _serve_model(self, job: dict, rel: str):
        """提供模型文件族：rel 为空时返回文件清单（供预览页索引）。"""
        if job["status"] != "done" or not job.get("result"):
            return self._json(409, {"error": "任务尚未完成"})
        base = job["result"].get("model_dir") or ""
        if not os.path.isdir(base):
            return self._json(404, {"error": "模型目录不存在"})
        base = os.path.realpath(base)
        if not rel:
            files = sorted(
                os.path.relpath(os.path.join(dp, f), base).replace(os.sep, "/")
                for dp, _, fs in os.walk(base) for f in fs
            )
            return self._json(200, {"dir": os.path.basename(base), "files": files})
        target = os.path.realpath(os.path.join(base, rel))
        # 防目录穿越
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            return self._json(404, {"error": "文件不存在"})
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as f:
            return self._send(200, f.read(), ctype)

    def do_POST(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if path != "/api/jobs":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._json(400, {"error": "缺少上传内容"})
        if length > MAX_UPLOAD:
            return self._json(413, {"error": f"文件过大（上限 {MAX_UPLOAD // 1024 // 1024}MB）"})

        raw_name = (qs.get("name") or ["upload.png"])[0]
        safe = "".join(c for c in os.path.basename(raw_name) if c.isalnum() or c in "._- ()")
        if not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".psd", ".psb", ".zip")):
            return self._json(400, {"error": "只支持 png / jpg / webp / bmp / psd / zip"})

        data = self.rfile.read(length)
        job_id = _new_job(os.path.splitext(safe)[0], {
            "resolution": float((qs.get("resolution") or ["1280"])[0]),
            "seed": float((qs.get("seed") or ["42"])[0]),
            "tblr_split": (qs.get("tblr_split") or ["false"])[0].lower() == "true",
        })
        job_dir = os.path.join(JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        image_path = os.path.join(job_dir, safe)
        with open(image_path, "wb") as f:
            f.write(data)
        _append_log(job_id, f"已接收 {safe}（{length / 1024:.0f} KB）")

        t = threading.Thread(target=_run_job, args=(job_id, image_path, job_dir, _jobs[job_id]["params"]),
                             daemon=True, name=f"job-{job_id}")
        t.start()
        return self._json(202, {"id": job_id})


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live2D 一站式流水线</title>
<style>
  :root{--bg:#0e1116;--card:#171b22;--line:#262c36;--fg:#e6e9ef;--dim:#8b95a5;
        --accent:#5b8cff;--ok:#3ecf8e;--err:#ff6b6b}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",system-ui,sans-serif}
  .wrap{max-width:860px;margin:0 auto;padding:40px 20px 80px}
  h1{font-size:26px;margin:0 0 6px}
  .sub{color:var(--dim);margin-bottom:28px}
  .pipe{display:flex;gap:8px;align-items:center;color:var(--dim);font-size:13px;margin-bottom:24px;flex-wrap:wrap}
  .pipe b{color:var(--fg);font-weight:600}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:18px}
  #drop{border:2px dashed var(--line);border-radius:12px;padding:40px 20px;text-align:center;
        cursor:pointer;transition:.15s}
  #drop:hover,#drop.over{border-color:var(--accent);background:#1b2231}
  #drop .big{font-size:17px;margin-bottom:6px}
  #drop .small{color:var(--dim);font-size:13px}
  #preview{max-width:180px;max-height:180px;border-radius:8px;margin-top:14px;display:none}
  .row{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:16px}
  label{font-size:13px;color:var(--dim)}
  input[type=number]{background:#0e1116;border:1px solid var(--line);color:var(--fg);
        border-radius:8px;padding:7px 10px;width:96px;font:inherit;font-size:14px}
  button{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:11px 22px;
        font:inherit;font-weight:600;cursor:pointer;transition:.15s}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.ghost{background:#232a35;color:var(--fg)}
  .bar{height:7px;background:#0e1116;border-radius:99px;overflow:hidden;margin:16px 0 8px}
  .bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--ok));
        transition:width .4s}
  .meta{display:flex;justify-content:space-between;font-size:13px;color:var(--dim)}
  .logs{background:#0b0e13;border:1px solid var(--line);border-radius:9px;padding:12px;
        font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;color:#9fb0c7;
        max-height:240px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  .done{color:var(--ok)}.err{color:var(--err)}
  a.dl{display:inline-block;background:var(--ok);color:#04150c;text-decoration:none;
        padding:11px 22px;border-radius:9px;font-weight:700;margin-right:10px}
  .files{font-size:13px;color:var(--dim);margin-top:12px}
  .files code{color:#9fb0c7;font-size:12px}
  .hidden{display:none}
</style>
</head>
<body>
<div class="wrap">
  <h1>Live2D 一站式流水线</h1>
  <div class="sub">单张插画 → 自动拆层 → 自动绑定 → 可用的 Live2D 模型（.moc3 / .cmo3）</div>
  <div class="pipe">
    <b>see-through</b><span>图层拆分</span> <span>→</span>
    <b>分层 PSD</b> <span>→</span>
    <b>psd2live</b><span>自动绑定</span> <span>→</span>
    <b>Live2D 模型</b>
  </div>

  <div id="tokenwarn" class="card hidden"
       style="border-color:#5a4a1f;background:#1d1a10;padding:14px 18px;font-size:14px"></div>

  <div class="card">
    <div id="drop">
      <div class="big">拖入插画，或点击选择</div>
      <div class="small">PNG / JPG / WEBP / BMP → 自动拆层+绑定<br>已有分层 <b>PSD</b>（或含 PSD 的 <b>zip</b>）→ 跳过拆层直接绑定，不消耗额度</div>
      <img id="preview" alt="">
    </div>
    <div class="row">
      <div><label>拆分分辨率</label><br><input type="number" id="res" value="1280" min="256" max="2048" step="64"></div>
      <div><label>随机种子</label><br><input type="number" id="seed" value="42"></div>
      <div><label>左右拆分</label><br>
        <select id="tblr" style="background:#0e1116;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:7px 10px;font:inherit">
          <option value="false">否</option><option value="true">是（手/眼分开）</option>
        </select></div>
      <div style="align-self:flex-end"><button id="go" disabled>开始生成</button></div>
    </div>
  </div>

  <div class="card hidden" id="progcard">
    <div class="meta"><span id="stage">准备中</span><span id="pct">0%</span></div>
    <div class="bar"><i id="bar"></i></div>
    <div class="meta"><span id="msg"></span><span id="elapsed"></span></div>
    <div class="logs" id="logs"></div>
    <div id="result" class="hidden" style="margin-top:16px">
      <a class="dl" id="dl" href="#">下载模型包 (.zip)</a>
      <a class="dl" id="pv" href="#" target="_blank"
         style="background:#5b8cff;color:#fff">预览模型</a>
      <div class="files" id="files"></div>
    </div>
  </div>
</div>

<script>
let file=null, jobId=null, timer=null, t0=0;
const $ = s => document.querySelector(s);
const link=(h,t)=>'<a href="'+h+'" target="_blank" style="color:#5b8cff">'+t+'</a>';
const code=s=>'<code style="color:#9fb0c7">'+s+'</code>';

fetch('/api/health').then(r=>r.json()).then(h=>{
  const w=$('#tokenwarn');
  w.innerHTML = (h.has_token
      ? '✅ 在线额度已配置（'+h.backend_label+'）'
      : '⚠️ <b>未配置在线额度 token</b>：匿名额度每天仅 1~2 次，极易用尽。<br>'
        +'免费获取 '+link('https://modelscope.cn/my/myaccesstoken','ModelScope')+'（国内直连）或 '
        +link('https://huggingface.co/settings/tokens','HuggingFace')+'，'
        +'然后写进 '+code('stack/.env')+'并重启服务。<br>'
        +'<span style="color:#8b95a5">已有分层 PSD？直接拖进来即可跳过拆层，不消耗额度。</span>')
    + '<div style="margin-top:10px"><button class="ghost" id="chk" style="font-size:13px;padding:7px 14px">检测各后端连通性</button></div>'
    + '<div id="chkres" style="margin-top:8px;font-size:13px;color:#8b95a5"></div>';
  w.classList.remove('hidden');
  $('#chk').onclick = async () => {
    $('#chkres').textContent = '检测中...';
    try{
      const r = await fetch('/api/backends'); const j = await r.json();
      $('#chkres').innerHTML = j.backends.map(b =>
        (b.ok?'✅ ':'❌ ') + '<b>' + b.label + '</b> — '
        + b.reason.replace(/\n/g,' ').slice(0,110)).join('<br>');
    }catch(e){ $('#chkres').textContent = '检测失败: ' + e.message; }
  };
}).catch(()=>{});
const drop=$('#drop'), input=Object.assign(document.createElement('input'),{type:'file',accept:'image/*,.psd,.psb,.zip'});

drop.onclick=()=>input.click();
input.onchange=()=>input.files[0]&&pick(input.files[0]);
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',ev=>{const f=ev.dataTransfer.files[0];f&&pick(f)});

function pick(f){
  const ext=(f.name.split('.').pop()||'').toLowerCase();
  if(!f.type.startsWith('image/') && !['psd','psb','zip'].includes(ext)){alert('请选择图片、分层 PSD 或 zip');return}
  file=f; $('#go').disabled=false;
  const r=new FileReader(); r.onload=e=>{$('#preview').src=e.target.result;$('#preview').style.display='block'};
  r.readAsDataURL(f);
  $('#drop .big').textContent=f.name;
  $('#drop .small').textContent=(f.size/1024).toFixed(0)+' KB';
}

$('#go').onclick=async()=>{
  if(!file)return;
  $('#go').disabled=true; $('#progcard').classList.remove('hidden');
  $('#result').classList.add('hidden'); $('#logs').textContent=''; 
  const q=new URLSearchParams({name:file.name,resolution:$('#res').value,seed:$('#seed').value,tblr_split:$('#tblr').value});
  try{
    const r=await fetch('/api/jobs?'+q,{method:'POST',body:file});
    const j=await r.json();
    if(!r.ok)throw new Error(j.error||'提交失败');
    jobId=j.id; t0=Date.now(); poll();
  }catch(e){ showErr(e.message); $('#go').disabled=false; }
};

function showErr(m){ $('#stage').textContent='失败'; $('#stage').className='err'; $('#msg').textContent=m; }

function poll(){
  clearTimeout(timer);
  timer=setTimeout(async()=>{
    try{
      const r=await fetch('/api/jobs/'+jobId); const j=await r.json();
      $('#stage').textContent={queued:'排队中','layer-split':'① 图层拆分（在线）',rigging:'② 自动绑定（本地）',done:'完成'}[j.stage]||j.stage;
      $('#stage').className = j.status==='done'?'done':(j.status==='error'?'err':'');
      $('#pct').textContent=Math.round(j.progress*100)+'%';
      $('#bar').style.width=(j.progress*100)+'%';
      $('#msg').textContent=j.message||'';
      $('#elapsed').textContent=((Date.now()-t0)/1000).toFixed(0)+'s';
      if(j.logs)$('#logs').textContent=j.logs.join('\n');
      $('#logs').scrollTop=$('#logs').scrollHeight;
      if(j.status==='done'){
        $('#dl').href='/api/jobs/'+jobId+'/download';
        $('#pv').href='/preview?job='+jobId;
        $('#files').innerHTML='<code>'+j.result.model_dir+'</code>'
          + '<br>主文件: <code>'+j.result.moc3.split('/').pop()+'</code>'
          + '<br>共 '+(j.result.files?j.result.files.length:0)+' 个文件';
        $('#result').classList.remove('hidden');
        $('#go').disabled=false; return;
      }
      if(j.status==='error'){ showErr(j.error); $('#go').disabled=false; return; }
      poll();
    }catch(e){ poll(); }
  }, 1200);
}
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Live2D 一站式服务")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    a = ap.parse_args()

    os.makedirs(JOBS_DIR, exist_ok=True)
    try:
        exe, prefix = pipeline.find_psd2live()
        print(f"✅ psd2live 就绪: {exe} {' '.join(prefix)}")
    except Exception as e:
        print(f"⚠️  {e}")
    sc = pipeline.seethrough_client
    cur = sc._backend(sc.DEFAULT_BACKEND)
    print(f"✅ 拆层后端: {cur['label']}（{sc.DEFAULT_BACKEND}）"
          + ("" if cur["token"] else "  ⚠️ 未配置 token —— 匿名额度每天仅 1~2 次"))
    if not cur["token"]:
        print(f"   配置（免费）: export {cur['token_env']}=<token>   {cur['signup']}")
    url = f"http://{a.host}:{a.port}"
    print(f"\n🚀 服务已启动: {url}\n")
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())