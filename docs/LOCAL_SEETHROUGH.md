# 本地部署 see-through（不使用 ModelScope）

把图层拆分跑在**你自己的机器**上：不需要任何令牌、不联网（首下权重后）、没有额度限制、没有排队。

适合：**有 NVIDIA 显卡的台式机 / 服务器**。

---

## 先说实话：什么机器能跑，什么机器不能

| 硬件 | 能否本地跑 | 说明 |
|---|---|---|
| **NVIDIA GPU，≥16GB 显存** | ✅ 推荐 | 1280 分辨率顺畅 |
| **NVIDIA GPU，12GB 显存** | ✅ 可以 | 建议加 `--group_offload`（见下） |
| **NVIDIA GPU，8GB 显存** | ⚠️ 勉强 | 必须 offload，会明显变慢 |
| **Apple Silicon** | ❌ 不行 | 见下方说明 |
| **纯 CPU** | ❌ 不实用 | 权重 12GB 装不下，且慢到无法使用 |
| **AMD ROCm** | ⚠️ 理论可行 | 上游给了 ROCm 指引，本指南未验证 |

### 为什么 Apple Silicon 不行

这不是"慢"，是**不可行**，两条硬性原因：

1. **权重约 12 GB**（LayerDiff 3D 的 UNet 单文件就 8.1 GB），而 see-through 代码**硬编码 CUDA**——全仓库 55 处 `cuda`，包括 `enable_group_offload('cuda', ...)` 这类显存优化 API，它们**在 MPS 上不存在**。仓库里没有任何 MPS 支持代码。
2. 即便改造成 MPS，8GB 统一内存也装不下。量化路径同样走不通——上游的 `inference_psd_quantized.py` 用 bitsandbytes，而**bnb 不支持 macOS**。

如果你只有 Mac，请用在线后端（ModelScope / HuggingFace），或用 **路径 B**（拖入已有分层 PSD，纯本地绑定）。
详见 README 的[两条使用路径](../README.md#两条使用路径)。

---

## 一、准备环境

### 1. 装 conda（没有的话）

推荐 [Miniconda](https://docs.conda.io/en/latest/miniconda.html)。

### 2. 创建环境并装 PyTorch（CUDA 版）

```bash
conda create -n see_through python=3.12 -y
conda activate see_through

# CUDA 12.8
pip install torch==2.8.0+cu128 torchvision==0.23.0+cu128 torchaudio==2.8.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128
```

> **CUDA 版本对不上？** 到 [PyTorch 官网](https://pytorch.org/get-started/locally/) 选你驱动对应的版本。
> 显存 12–16GB 建议装对应版本的 `cu121`/`cu124` 也可以，关键是**能 `import torch` 且 `torch.cuda.is_available()` 为 True**。

### 3. 装 see-through 依赖

```bash
git clone https://github.com/shitagaki-lab/see-through.git
cd see-through
pip install -r requirements.txt

# 建立 assets 软链（脚本按仓库根目录查找素材）
ln -sf common/assets assets
```

> **Windows 用户**：没有 `ln` 命令，改用目录联接或直接复制：
> ```bat
> mklink /J assets common\assets
> rem 若 mklink 不可用（需管理员或开发者模式），就直接复制：
> xcopy /E /I common\assets assets
> ```

**可选依赖装不装？**

`requirements.txt` 里有一批**可选 tier**（detectron2 / SAM2 / mmcv+mmdet）。**做图层拆分不需要它们**——
`inference_psd.py` 的基础流程只依赖 `requirements.txt` 里的核心包（torch / diffusers / transformers /
psd-tools / opencv 等）。别装那些 tier，detectron2 在 Windows 上编译尤其痛苦。

### 4. 验证环境

```bash
python -c "import torch; print('CUDA:', torch.cuda.is_available(), '| 显卡:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无')"
```

必须输出 `CUDA: True`。否则先解决驱动/PyTorch 版本问题，再往下走。

---

## 二、下载模型权重

**首次运行时会自动从 HuggingFace 下载**，共约 12 GB：

| 模型 | 仓库 | 体积 | 作用 |
|---|---|---|---|
| LayerDiff 3D | `layerdifforg/seethroughv0.0.2_layerdiff3d` | 10.2 GB | 生成透明图层（SDXL） |
| Marigold Depth | `24yearsold/seethroughv0.0.1_marigold` | ~1.2 GB | 深度估计（排绘制顺序） |
| SAM 身体解析 | `24yearsold/l2d_sam_iter2` | 1.3 GB | 语义部件分割 |

**国内网络建议先配镜像**，否则会很慢或超时：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

想提前下载到指定目录（便于复用 / 离线迁移）：

```bash
pip install -U "huggingface_hub[cli]"
export HF_ENDPOINT=https://hf-mirror.com
hf download layerdifforg/seethroughv0.0.2_layerdiff3d --local-dir ./weights/layerdiff3d
hf download 24yearsold/seethroughv0.0.1_marigold --local-dir ./weights/marigold
hf download 24yearsold/l2d_sam_iter2 --local-dir ./weights/sam
```

默认缓存位置：`~/.cache/huggingface/`（Windows：`C:\Users\<你>\.cache\huggingface\`）。
**确保它有 ≥15 GB 空余**（模型 + 推理中间结果）。

---

## 三、先单独跑通一次

**务必在仓库根目录运行**（脚本用相对路径找模块）：

```bash
cd see-through
conda activate see_through
export HF_ENDPOINT=https://hf-mirror.com

python inference/scripts/inference_psd.py \
  --srcp common/assets/test_image.png \
  --save_to_psd
```

产出默认在 `workspace/layerdiff_output/`，其中包含分层 `.psd`。

显存吃紧时加 offload（更慢但更省显存）：

```bash
python inference/scripts/inference_psd.py \
  --srcp common/assets/test_image.png \
  --save_to_psd \
  --group_offload
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--srcp` | 输入图片**或目录**（目录会批量处理） |
| `--save_to_psd` | 导出分层 PSD（不加则只出中间结果） |
| `--resolution 1280` | 推理分辨率（默认 1280） |
| `--resolution_depth 768` | 深度模型分辨率；设 `-1` 与主模型对齐 |
| `--inference_steps 30` | 扩散步数（降低可提速） |
| `--seed 42` | 换种子会得到不同的图层划分 |
| `--tblr_split` | 左右部件分离（手 / 眼 / 眉单独成层，**绑定效果更好**） |
| `--group_offload` | 分块卸载到 CPU，省显存、更慢 |
| `--disable_progressbar` | 关闭进度条（写日志时更干净） |

---

## 四、接进本服务

跑通之后，让服务用本地后端即可——**不需要改任何代码**：

编辑 `stack/.env`：

```ini
SEETHROUGH_BACKEND=local
SEETHROUGH_REPO=/绝对路径/to/see-through
SEETHROUGH_LOCAL_PYTHON=/绝对路径/to/miniconda3/envs/see_through/bin/python
```

**关键在 `SEETHROUGH_LOCAL_PYTHON`**：见下文。Windows 示例：

```ini
SEETHROUGH_BACKEND=local
SEETHROUGH_REPO=D:\live2d\see-through
SEETHROUGH_LOCAL_PYTHON=C:\Users\you\miniconda3\envs\see_through\python.exe
```

然后重启服务：`./start.sh`（Windows `start.bat`）。

### 为什么必须指定 Python 路径

see-through 装在 conda 环境里，而本服务默认用系统 Python。
不指定的话，服务会去调用一个**没装 torch 的解释器**，直接报 `ModuleNotFoundError`。

确认路径：

```bash
# macOS / Linux
conda activate see_through && which python
# Windows
conda activate see_through && where python
```

把输出的**完整路径**填进 `SEETHROUGH_LOCAL_PYTHON`。

### 验证

```bash
cd stack
python selfcheck.py
```

期望看到：

```
✅ 本地 see-through: 本地仓库就绪，CUDA 可用
```

如果显示 `CUDA 不可用`，说明你填的 Python 不是装好 CUDA 版 torch 的那个，回到第三步检查。

---

## 五、可选配置

```ini
# 本地推理超时（秒）。默认 7200（2 小时）。低显存 + offload 可能很慢，可调大
SEETHROUGH_LOCAL_TIMEOUT=10800
```

服务调用本地后端时**固定加上 `--save_to_psd`**（否则拿不到 PSD），
并按 `.env` 或页面参数传递 `--resolution` / `--seed` / `--tblr_split`。

---

## 六、故障排查

| 现象 | 原因与解决 |
|---|---|
| `ModuleNotFoundError: No module named 'torch'` | `SEETHROUGH_LOCAL_PYTHON` 指向的解释器不对（最常见） |
| `CUDA out of memory` | 加 `--group_offload`；或降低 `--resolution`；或关掉其它占显存程序 |
| 提示找不到 `inference_psd.py` | `SEETHROUGH_REPO` 路径不对，或没设置 |
| 找不到 `assets/` | 忘了 `ln -sf common/assets assets`（Windows 用 `mklink /J`） |
| `ModuleNotFoundError: No module named 'common'` | 没在仓库根目录运行；本服务已用 `cwd=repo` 处理，手动跑时注意 `cd see-through` |
| 下载权重卡住/超时 | 设 `HF_ENDPOINT=https://hf-mirror.com` |
| 报错涉及 `detectron2` / `mmdet` | 你多装了可选 tier；拆层不需要它们，建议新建干净环境只装 `requirements.txt` |
| 推理极慢 | 正常现象：这是 SDXL 级别的扩散模型。12GB 显存 + offload 下每张图十几分钟起 |
| 换机器后要重下权重 | 整个 `~/.cache/huggingface/hub` 拷过去即可 |

---

## 附：与在线后端的对比

| | 本地 GPU | ModelScope | HuggingFace |
|---|---|---|---|
| 需要令牌 | ❌ 不需要 | ✅ 需要 | 可选 |
| 需要联网 | 仅首次下权重 | ✅ 每次 | ✅ 每次 |
| 额度限制 | 无 | 有（免费额度） | 匿名极少 |
| 实测耗时 | 取决于显卡 | 约 26 分钟 | 约 2.7 分钟 |
| 隐私 | **图片不出本机** | 上传到第三方 | 上传到第三方 |
| 硬件门槛 | NVIDIA ≥12GB | 无 | 无 |

**没有 N 卡也不用勉强**：走在线后端，或用路径 B（已有 PSD → 本地绑定），
后者完全不需要 GPU。详见 [README](../README.md)。
