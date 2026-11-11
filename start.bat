@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo 需要 Python 3.10+（建议 3.12）。
  exit /b 1
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 需要 Python 3.10+。
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [start] 创建虚拟环境 .venv …
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [start] 创建虚拟环境失败。
    exit /b 1
  )
)

set "PY=.venv\Scripts\python.exe"

echo [start] 升级 pip …
"%PY%" -m pip install --upgrade pip >nul 2>&1

echo [start] 安装依赖（默认 PyPI）…
"%PY%" -m pip install -r requirements.txt
if not errorlevel 1 goto :run

echo [start] 默认源失败，改用国内镜像…
call :try_mirror "https://pypi.tuna.tsinghua.edu.cn/simple" "pypi.tuna.tsinghua.edu.cn"
if not errorlevel 1 goto :run
call :try_mirror "https://mirrors.aliyun.com/pypi/simple" "mirrors.aliyun.com"
if not errorlevel 1 goto :run
call :try_mirror "https://pypi.mirrors.ustc.edu.cn/simple" "pypi.mirrors.ustc.edu.cn"
if not errorlevel 1 goto :run
call :try_mirror "https://mirror.baidu.com/pypi/simple" "mirror.baidu.com"
if not errorlevel 1 goto :run

echo [start] 依赖安装失败：默认源与国内镜像均不可用。
exit /b 1

:try_mirror
echo [start] 尝试镜像: %~1
"%PY%" -m pip install -r requirements.txt -i "%~1" --trusted-host "%~2"
if errorlevel 1 exit /b 1
exit /b 0

:run
"%PY%" -m server.init_data
if errorlevel 1 exit /b 1
"%PY%" -m server.check_database
if errorlevel 1 exit /b 1
"%PY%" -m uvicorn server.app:app --host 0.0.0.0 --port 50025 --no-proxy-headers
endlocal
