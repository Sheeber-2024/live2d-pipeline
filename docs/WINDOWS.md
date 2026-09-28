# Windows 部署指南

本指南适用于 **Windows 10 / 11 x64**。全程只需命令行基础，不需要 Visual Studio 或 C++ 工具链。

> **好消息**：psd2live 官方发布 Windows 安装包（EXE / MSI / 便携 ZIP），自带你需要的 Java 运行时。
> 如果你不想从源码构建，可以先从 [psd2live Releases](https://github.com/tsunehimatoi/psd2live/releases/latest)
> 下载装好，再用本仓库的服务去调用它——见下方「[方式二](#方式二用官方安装包跳过构建)」。

---

## 前置条件

| 项目 | 要求 | 怎么装 |
|---|---|---|
| **Python** | 3.9+（建议 3.11/3.12） | [python.org](https://www.python.org/downloads/windows/) 下载时**务必勾选 "Add python.exe to PATH"** |
| **Git** | 任意版本 | [git-scm.com](https://git-scm.com/download/win) |
| **JDK 21** | Temurin 21 | [Adoptium](https://adoptium.net/temurin/releases/?version=21&os=windows) 选 `.msi`，安装时勾选 **Set JAVA_HOME** |
| 显卡驱动 | 若要跑本地拆层 | NVIDIA 驱动（仅 `local` 后端需要） |

本仓库的服务本身**只用 Python 标准库**，不需要 `pip install` 任何东西。

验证环境：

```bat
python --version
git --version
java -version
```

> 如果 `python` 打开的是 Microsoft Store，请到「设置 → 应用 → 高级应用设置 → 应用执行别名」里
> 关掉 `python.exe` 和 `python3.exe` 两个别名，然后用 `where python` 确认指向真实安装路径。

---

## 快速开始

### 方式一：从源码构建（推荐，全自动）

```bat
git clone https://github.com/<你的仓库>.git live2d-pipeline
cd live2d-pipeline

git clone https://github.com/shitagaki-lab/see-through.git see-through
git clone https://github.com/tsunehimatoi/psd2live.git psd2live

cd stack
copy .env.example .env
notepad .env
start.bat
```

`start.bat` 会自动：

1. 检查 Java 21（没有会给出下载链接并退出）
2. 首次运行时构建 psd2live（`gradlew.bat createDistributable`，约 5–10 分钟）
3. 下载预览用的三个渲染库到 `web\vendor\`
4. 启动服务并打开浏览器

想只准备环境不启动：`start.bat --setup`。

### 方式二：用官方安装包（跳过构建）

不想跑 Gradle 构建的话：

1. 从 [psd2live Releases](https://github.com/tsunehimatoi/psd2live/releases/latest) 下载 **便携版 ZIP**，解压到任意目录（例如 `D:\PSD2Live`）
2. 告诉服务去哪里找它：

```bat
set PSD2LIVE_APP=D:\PSD2Live
```

或写进 `.env`：

```ini
PSD2LIVE_APP=D:\PSD2Live
```

3. 正常执行 `start.bat`

服务会自动探测以下任一布局：

```
D:\PSD2Live\PSD2Live.exe                    ← 便携版
D:\PSD2Live\app\*.jar + runtime\bin\java.exe ← 自带运行时
```

> 便携版 ZIP 的目录结构可能与上面略有差异。若探测失败，`start.bat` 会明确报告它找了哪些路径。
> 也可以用 `PSD2LIVE_JAVA` 指定 java.exe 让它走 `java -cp` 方式启动。

---

## 配置拆层后端

服务默认走在线后端。**Windows 上你还有一个更好的选择：本地 GPU。**

### 推荐：本地 GPU（无需令牌、无需联网）

如果你的机器有 **NVIDIA 显卡（建议 ≥12GB 显存）**：

```ini
SEETHROUGH_BACKEND=local
SEETHROUGH_REPO=D:\path\to\see-through
```

然后在**同一个 Python 环境**里装依赖、下权重——完整步骤见
**[LOCAL_SEETHROUGH.md](LOCAL_SEETHROUGH.md)**。这是全程最快、且不受任何额度限制的方案。

### 备选：在线后端（免费令牌）

| 平台 | 地址 | 备注 |
|---|---|---|
| ModelScope（国内直连） | https://modelscope.cn/my/myaccesstoken | 约 26 分钟（慢在排队） |
| HuggingFace | https://huggingface.co/settings/tokens | 约 2.7 分钟，需能访问外网 |

```ini
MODELSCOPE_TOKEN=ms-xxxxxxxx
SEETHROUGH_BACKEND=modelscope
SEETHROUGH_TIMEOUT=2700
```

### 完全不用拆层

没有显卡也不想配令牌？直接用**路径 B**：把已有的分层 PSD（或含 PSD 的 zip）
拖进服务，纯本地绑定出模型。也可以用 ModelScope 的
[网页版 demo](https://modelscope.cn/studios/ljsabc/See-Through)（登录即可，无需令牌）
拆好再拖进来。

---

## 验证安装

```bat
cd stack
python selfcheck.py
```

期望输出里这几行是 ✅：

```
✅ psd2live: PSD2Live.exe
✅ ModelScope: 可用（已认证）          ← 或 local: 本地仓库就绪，CUDA 可用
✅ 编排完成，用时 x.xs
✅ 产物验收通过（moc3 magic + 引用 + JSON，11 个文件）
✅ 全部自检通过
```

`local` 后端显示 ⚠️ 且提示 CUDA 不可用是**正常的**——那是可选能力，不影响其他后端。

也可以跑客户端协议自测（不联网、不耗额度）：

```bat
python test_seethrough_client.py
```

---

## Windows 特有注意事项

### 路径与编码

- `.env` 里的路径含空格时**不用加引号**，例如 `PSD2LIVE_APP=C:\Program Files\PSD2Live`
- 服务已强制 UTF-8 处理中文文件名；若遇到乱码，在 `cmd` 里先执行 `chcp 65001`

### 防火墙

首次启动时 Windows 会弹出防火墙提示。**选择「允许访问」，但只勾选「专用网络」**即可——
服务默认只监听 `127.0.0.1`，不需要对外网开放。

### 端口占用

```bat
set PORT=8888
start.bat
```

### Gradle 构建慢或失败

- 首次构建要下载 Gradle 发行版和依赖（约几百 MB），耐心等
- 构建内存已在 `start.bat` 里限制为 `-Xmx3g`，**不要调大**（8GB 内存机器调大反而会失败）
- 卡住时删掉 `psd2live\.gradle` 和 `%USERPROFILE%\.gradle\caches` 重试
- Windows Defender 实时保护可能拖慢构建，可临时把工作目录加入排除项

### 长路径限制

Windows 默认路径长度上限 260 字符。把仓库放在靠近根目录的位置（如 `D:\live2d`），
不要在深层嵌套的 `Desktop\新建文件夹\...` 里。或用管理员权限执行：

```bat
git config --system core.longpaths true
```

### psd2live GUI（可选）

想手工微调模型，可直接开 psd2live 的桌面编辑器：

```bat
..\psd2live\build\compose\binaries\main\app\PSD2Live\PSD2Live.exe
```

或在源码目录用 `run-gui.bat`。

> **GUI 有实例锁**：已经开着一个时再开会提示"已在运行"并退出。
> GUI 与命令行绑定**互斥**，不要同时跑。

**关于官方 Cubism 原生预览**（可选，非必需）：
psd2live 的**内置渲染完全够用**，不需要官方 SDK。只有想用官方运行时校验渲染与物理时才需要，
那要求 CMake 3.16+、Visual Studio 2022 C++ 工具链和本地 Cubism 5 SDK for Native (5-r.5)。
详见上游 `psd2live/docs/zh/guide/CUBISM_SDK_SETUP.md`。

---

## 常见问题

| 现象 | 解决 |
|---|---|
| `python` 不是内部或外部命令 | 重装 Python 并勾选 "Add to PATH"，或关掉商店别名 |
| `java` 不是内部或外部命令 | 装 Temurin 21 并勾选 Set JAVA_HOME，或设 `PSD2LIVE_JAVA` |
| `start.bat` 一闪而过 | 在 `cmd` 里手动执行 `start.bat` 看报错 |
| 构建后仍提示找不到 psd2live | 确认 `psd2live\build\compose\binaries\main\app\PSD2Live\PSD2Live.exe` 存在；否则 `start.bat --setup` 重跑 |
| 浏览器没自动打开 | 手动访问 `http://127.0.0.1:8770` |
| 拖入 PSD 报"zip 内未找到 PSD" | 上传的 zip 里确实没有 `.psd`/`.psb`，或文件名是中文导致解压异常 |
| 绑定很慢 | 首次绑定要加载模型，之后会快；8GB 内存机器尤其明显 |

---

## 下一步

- 模型预览与参数调试：README 的[模型预览](../README.md#模型预览)一节
- 本地跑 see-through（不用任何在线服务）：[LOCAL_SEETHROUGH.md](LOCAL_SEETHROUGH.md)
- 模型导入 VTube Studio：把整个输出文件夹放进去，选 `*.model3.json`
