# Live2D 一站式流水线

把**单张动漫插画**变成**可用的 Live2D 模型**，在一个服务里串起两个开源工程：

```
插画.png
   │  ① see-through       单图图层拆分（SIGGRAPH 2026）
   ▼
分层 PSD（最多 23 个语义图层，含遮挡补全与绘制顺序）
   │  ② psd2live          自动绑定 / 网格 / 物理 / 动作
   ▼
Live2D 模型：.moc3 + model3.json + 动作 + 物理 + .cmo3（可继续在 Cubism 编辑）
```

自带 Web 界面、浏览器内预览、四种拆层后端（在线 / 本地 GPU），以及不依赖任何在线服务的纯本地路径。

---

## 目录

- [快速开始](#快速开始)
- [拆层后端怎么选](#拆层后端怎么选)
- [两条使用路径](#两条使用路径)
- [产物说明](#产物说明)
- [模型预览](#模型预览)
- [自检](#自检)
- [命令行用法](#命令行用法)
- [故障排查](#故障排查)
- [部署指南](#部署指南)
- [许可](#许可)

---

## 快速开始

### macOS / Linux

```bash
git clone https://github.com/Sheeber-2024/live2d-pipeline.git live2d-pipeline
cd live2d-pipeline

# 1. 取得两个上游工程（本仓库不再分发第三方代码）
git clone https://github.com/shitagaki-lab/see-through.git see-through
git clone https://github.com/tsunehimatoi/psd2live.git psd2live

# 2. 配置拆层后端（见下文，也可跳过先用纯本地路径）
cd stack
cp .env.example .env && open -e .env

# 3. 启动
./start.sh
```

浏览器会自动打开 `http://127.0.0.1:8770`，拖入图片即可。

首次运行 `start.sh` 会自动：下载 JDK 21（仅构建需要）→ 构建 psd2live → 下载预览渲染库。

### Windows

见 **[docs/WINDOWS.md](docs/WINDOWS.md)**，或直接：

```bat
git clone https://github.com/Sheeber-2024/live2d-pipeline.git live2d-pipeline
cd live2d-pipeline
git clone https://github.com/shitagaki-lab/see-through.git see-through
git clone https://github.com/tsunehimatoi/psd2live.git psd2live
cd stack
copy .env.example .env
notepad .env
start.bat
```

---

## 拆层后端怎么选

第 ① 步「单图拆层」有四种后端，在 `.env` 里用 `SEETHROUGH_BACKEND` 指定，失败会自动按序回退。

| 后端 | 值 | 是否需要令牌 | 实测耗时 | 适合谁 |
|---|---|---|---|---|
| **本地 see-through** | `local` | 不需要 | 取决于你的 GPU | **有 NVIDIA 显卡的机器**，完全离线、无额度限制 |
| ModelScope | `modelscope` | 需要（免费） | 约 **26 分钟** | 国内直连；慢在排队，与分辨率无关 |
| HuggingFace | `hf` | 可选（免费） | 约 **2.7 分钟** | 有梯子、想要速度 |
| HF 量化版 | `hf-nf4` | 可选（免费） | 同 `hf` | 作为额外额度池 |

### 拿令牌（免费）

| 平台 | 地址 |
|---|---|
| ModelScope（国内推荐） | https://modelscope.cn/my/myaccesstoken |
| HuggingFace | https://huggingface.co/settings/tokens |

填进 `stack/.env`：

```ini
MODELSCOPE_TOKEN=ms-xxxxxxxx
SEETHROUGH_BACKEND=modelscope
SEETHROUGH_TIMEOUT=2700     # ModelScope 排队较久，建议放宽（默认 900 秒会提前放弃）
```

> **实测数据**（本机 M1、已认证 ModelScope）：1280 分辨率 1567 秒，768 分辨率 1624 秒。
> 两者几乎相同 → 耗时主要花在**排队**而非推理，所以**不必为省时间降分辨率**，直接用默认 1280。

### 用本地 GPU 跑（推荐给有 N 卡的人）

完全不需要令牌和联网，速度取决于显卡。配置方式见 **[docs/LOCAL_SEETHROUGH.md](docs/LOCAL_SEETHROUGH.md)**：

```ini
SEETHROUGH_BACKEND=local
SEETHROUGH_REPO=/path/to/see-through
SEETHROUGH_LOCAL_PYTHON=python
```

---

## 两条使用路径

服务支持两种输入。**路径 B 不需要任何令牌，现在就能用。**

### 路径 A：单张插画（需要拆层后端）

拖入 PNG / JPG / WEBP / BMP，服务自动走完拆层 → 绑定 → 打包。

### 路径 B：已有分层 PSD（纯本地，零依赖）

拖入 **PSD 或含 PSD 的 zip**，跳过在线拆层直接绑定。

PSD 来源不限：自己画的、Photoshop 做的、或从别处拆好的都行。
已实测兼容：see-through 的输出、psd2live 自带示例（`examples/tml`、`examples/ds`）。

> See-through 官方在 ModelScope 上也有**免费网页版 demo**（登录即可，无需令牌）：
> https://modelscope.cn/studios/ljsabc/See-Through
> 在网页上拆好、下载 PSD，再拖进本服务绑定——**完全免费**的完整流程。

---

## 产物说明

输出目录里是完整的模型文件族：

| 文件 | 用途 |
|---|---|
| `*.moc3` | Live2D 运行时模型，VTube Studio 等直接加载 |
| `*.model3.json` | 模型定义（贴图、动作、物理的索引） |
| `*.physics3.json` | 物理（前后发摆动、果冻眼） |
| `*.motion3.json` | 待机 / 眨眼 / 点头 / 摇头 |
| `*.4096/texture_00.png` | 贴图集 |
| `*.cmo3` | **Cubism Editor 可继续编辑的工程文件** |
| `*.psd2live.json` | 元数据：识别出的部件、变形器、参数清单 |

**导入 VTube Studio**：把整个文件夹放进去，选 `*.model3.json`。

---

## 模型预览

生成完成后点结果区的 **「预览模型」**，或直接访问：

```
http://127.0.0.1:8770/preview?job=<任务id>
```

用官方 Live2D Web 运行时渲染（pixi.js + pixi-live2d-display + Cubism Core）：

| 功能 | 说明 |
|---|---|
| 实时渲染 | WebGL 渲染真实 `.moc3`，拖拽可调整视角 |
| 参数滑块 | 列出全部 Live2D 参数并实时驱动 |
| 播放动作 | 自动列出模型里所有动作组，点击即播 |
| 命中区域 | 切换显示 hit area |
| 元信息 | 文件名、引用项、画布尺寸 |

渲染库由 `setup_vendor.py` 下载到 `web/vendor/`（首次由 `start.sh` 自动执行），
**下载后离线可用，不依赖 CDN**。

---

## 自检

两套检查，都不消耗在线额度：

```bash
cd stack

# 1. 环境 + 编排 + 产物完整性
python3 selfcheck.py

# 2. 在线客户端的协议与错误处理（本地 mock，7 个场景）
python3 test_seethrough_client.py
```

`selfcheck.py` 检查四项：

1. **psd2live 可用性** —— 定位到自带 JRE 的原生启动器（跨平台）
2. **各后端状态** —— 只探认证与连通，不消耗 GPU 额度
3. **编排逻辑** —— 用已有 PSD 模拟拆层，跑完整条 `run_pipeline` 成功路径
4. **产物完整性** —— `moc3` magic 头、`model3.json` 引用齐全、所有 JSON 有效

服务页面上的 **「检测各后端连通性」** 按钮做的是第 2 项，填完 token 点一下就知道通不通。

---

## 命令行用法

不启动服务也能用：

```bash
cd stack

# 完整流水线（插画 → 模型）
python3 pipeline.py 你的插画.png -o ./output

# 只要图层拆分
python3 seethrough_client.py 插画.png ./layers --backend modelscope

# 只要绑定（已有 PSD）
python3 pipeline.py --help    # 或用 psd2live 自带的 CLI，见下
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `--resolution 1280` | 拆分分辨率（默认 1280） |
| `--seed 42` | 换种子会得到不同的图层划分结果 |
| `--tblr-split` | 左右部件分离（手、眼、眉单独成层，绑定效果更好） |
| `--backend hf\|hf-nf4\|modelscope\|local` | 指定拆层后端 |

### 直接调 psd2live

```bash
# macOS
../psd2live/build/compose/binaries/main/app/psd2live.app/Contents/MacOS/PSD2Live \
  --input 分层.psd --output ./model --lang zh

# Windows
..\psd2live\build\compose\binaries\main\app\PSD2Live\PSD2Live.exe ^
  --input 分层.psd --output .\model --lang zh
```

### GUI 与 MCP（可选）

psd2live 自带桌面编辑器，用于手工修形、绑骨、调曲线，也可通过内置 MCP 服务让 Agent 驱动：

```bash
# macOS
open ../psd2live/build/compose/binaries/main/app/psd2live.app
# Windows
..\psd2live\build\compose\binaries\main\app\PSD2Live\PSD2Live.exe
```

| 项目 | 值 |
|---|---|
| MCP 端点 | `http://127.0.0.1:23871/mcp`（Streamable HTTP） |
| 认证 | Bearer token，未带 token 返回 401 |
| Token 位置 | macOS: `~/Library/Preferences/io.github.psd2live.plist` → `io/github/psd2live/agent/agent_mcp_bearer_token` |
| stdio 桥 | 上游仓库自带 `psd2live/mcp_proxy.py` |

> GUI 有**实例锁**：已有实例在跑时会提示"已在运行"并退出，这是设计行为。
> GUI 与命令行互斥，不要同时跑。

---

## 故障排查

| 现象 | 原因与解决 |
|---|---|
| `额度已用尽` / 服务端返回 `null` | 匿名额度用完；配置 token，或改用 `--backend local` |
| 推理超时（900s） | ModelScope 排队较久；设 `SEETHROUGH_TIMEOUT=2700` |
| `ModelScope 需要认证` | 必须配 `MODELSCOPE_TOKEN`，它不支持匿名 |
| `找不到 psd2live` | 跑 `./start.sh --setup`（Windows `start.bat --setup`）重新构建 |
| 绑定结果部件错乱 | 拆分时加 `--tblr-split`；或换 `--seed` 重试 |
| 预览页参数面板空白 | 正常降级：不同 pixi-live2d-display 版本 coreModel 结构不同，已做多路径兼容 |
| 预览页提示 WebGL 不支持 | 换用带 GPU 的浏览器/模式；headless `--disable-gpu` 下必然如此 |
| 服务端口被占用 | `PORT=8888 ./start.sh`（Windows `set PORT=8888 && start.bat`） |
| 构建时 Gradle 内存不足 | 启动脚本里已限制 `-Xmx3g`，勿改大 |
| macOS 上预览库下载 403 | 已内置浏览器 UA + 镜像回退；仍失败可手动放入 `web/vendor/` |

---

## 部署指南

| 场景 | 文档 |
|---|---|
| **Windows 部署** | [docs/WINDOWS.md](docs/WINDOWS.md) |
| **本地跑 see-through（不用 ModelScope，需 NVIDIA GPU）** | [docs/LOCAL_SEETHROUGH.md](docs/LOCAL_SEETHROUGH.md) |

---

## 目录结构

```
live2d-pipeline/
├── README.md
├── .gitignore
├── docs/
│   ├── WINDOWS.md              Windows 部署指南
│   └── LOCAL_SEETHROUGH.md     本地 see-through 部署指南
└── stack/                      ← 整合工程
    ├── start.sh                macOS / Linux 启动
    ├── start.bat               Windows 启动
    ├── serve.py                Web 服务（纯标准库）
    ├── pipeline.py             编排：拆层 → 绑定 → 打包
    ├── seethrough_client.py    拆层客户端（4 后端 + token + 回退）
    ├── setup_vendor.py         下载预览渲染库
    ├── selfcheck.py            自检：环境 + 编排 + 产物
    ├── test_seethrough_client.py  客户端协议自测（离线 mock）
    ├── test_mock_space.py      配套 mock Space 服务器
    ├── .env.example            token 配置模板
    └── web/preview.html        模型预览页
```

运行时会额外生成（已被 `.gitignore` 忽略）：`stack/tools/`（JDK）、`stack/web/vendor/`（渲染库）、`stack/jobs/`、`stack/out-*/`。

---

## 许可

**本仓库（整合层）**：只包含编排、服务、预览与文档代码。请自行选择许可（如 MIT）。

**上游工程**需单独 clone，各自遵循其原许可：

| 项目 | 许可 | 说明 |
|---|---|---|
| [shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through) | **Apache-2.0** | 单图图层拆分 |
| [tsunehimatoi/psd2live](https://github.com/tsunehimatoi/psd2live) | **GPL-3.0** | ⚠️ 有传染性；本项目通过**子进程调用其 CLI**（聚合而非衍生），但若你要分发其二进制，需遵循 GPL |

**预览页依赖**不由本仓库再分发：

| 库 | 许可 |
|---|---|
| [Live2D Cubism Core](https://www.live2d.com/en/sdk/download/web/) | Live2D Inc. 专有许可，由 `setup_vendor.py` 从官方地址获取 |
| pixi.js | MIT |
| pixi-live2d-display | MIT |

**关于 see-through 的模型权重**：约 12GB，托管在 HuggingFace，首次运行自动下载（见 `docs/LOCAL_SEETHROUGH.md`）。
