@echo off
rem Live2D 一站式服务 —— Windows 启动脚本
rem   start.bat          启动服务（自动打开浏览器）
rem   start.bat --setup  仅准备环境（构建 psd2live + 下载预览依赖）
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "HERE=%CD%"
for %%I in ("%HERE%\..") do set "WORKSPACE=%%~fI"
set "PSD2LIVE_DIR=%WORKSPACE%\psd2live"
set "APP=%PSD2LIVE_DIR%\build\compose\binaries\main\app\PSD2Live"
set "LAUNCHER=%APP%\PSD2Live.exe"
if "%PORT%"=="" set "PORT=8770"

rem 载入 .env（如果存在）
if exist "%HERE%\.env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%HERE%\.env") do (
    set "%%a=%%b"
  )
)

echo.
echo === Live2D 一站式服务 ===
echo.

rem ---- 1. JDK ----
if not "%JAVA_HOME%"=="" (
  if exist "%JAVA_HOME%\bin\java.exe" (
    echo [OK] 使用已有 JAVA_HOME: %JAVA_HOME%
    goto :jdk_done
  )
)
where java >nul 2>&1
if %errorlevel%==0 (
  echo [OK] 使用系统 PATH 中的 Java
  goto :jdk_done
)
echo [!] 未找到 Java 21。请安装 Temurin 21 并设置 JAVA_HOME：
echo     https://adoptium.net/temurin/releases/?version=21
echo     或设环境变量 PSD2LIVE_JAVA=^<java.exe 路径^>
exit /b 1
:jdk_done

rem ---- 2. 构建 psd2live ----
if exist "%LAUNCHER%" goto :psd_done
echo [*] 首次运行：构建 psd2live（约 5-10 分钟）
pushd "%PSD2LIVE_DIR%"
call gradlew.bat --no-daemon -Dorg.gradle.jvmargs="-Xmx3g" createDistributable
popd
if not exist "%LAUNCHER%" (
  echo [X] 构建失败：未生成 %LAUNCHER%
  exit /b 1
)
echo [OK] psd2live 构建完成
:psd_done

rem ---- 3. 预览依赖 ----
if not exist "%HERE%\web\vendor\live2dcubismcore.min.js" (
  echo [*] 下载预览渲染库 ...
  python "%HERE%\setup_vendor.py"
)

rem ---- 4. token 提示 ----
if "%MODELSCOPE_TOKEN%"=="" if "%HF_TOKEN%"=="" (
  echo [!] 未配置 token：匿名额度每天仅 1~2 次拆层
  echo     ModelScope^(国内推荐^): https://modelscope.cn/my/myaccesstoken
  echo       echo MODELSCOPE_TOKEN=你的token^> .env
  echo     HuggingFace: https://huggingface.co/settings/tokens
)

if /i "%~1"=="--setup" (
  echo [OK] 环境准备完成
  exit /b 0
)

echo [*] 启动服务 http://127.0.0.1:%PORT%
start "" http://127.0.0.1:%PORT%
python "%HERE%\serve.py" --port %PORT%
endlocal
