@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo 帝蓝工作流 · 文件夹升级数据搬家（V27 → V38）
echo ------------------------------------------------------------
echo 用途：仓库里的整合版文件夹已从 -V27 改名为 -V38。
echo       你本地的项目/素材/输出等运行数据不进 git，还留在旧
echo       文件夹里。本脚本把它们复制到新文件夹（只复制运行数据，
echo       不覆盖任何程序代码文件）。
echo 说明：数据中心 / 统一审核端 / 分镜模块已从工作流移除，
echo       本脚本只迁移 美术(material) 与 视频(video) 两个模块。
echo ============================================================
echo.

set OLDI=帝蓝工作流-整合版-V27
set NEWI=帝蓝工作流-整合版-V38

if not exist "%OLDI%" (
  echo 没有发现旧的 -V27 文件夹，无需迁移。
  pause
  exit /b 0
)

echo [1/1] 整合版（美术/视频 两模块的项目与素材数据）...
for %%M in (material video) do (
  for %%D in (projects input output exports snapshots operation_logs temp) do (
    if exist "%OLDI%\tools\%%M\%%D" robocopy "%OLDI%\tools\%%M\%%D" "%NEWI%\tools\%%M\%%D" /E /NFL /NDL /NJH /NJS >nul
  )
  if exist "%OLDI%\tools\%%M\usage.json" copy /Y "%OLDI%\tools\%%M\usage.json" "%NEWI%\tools\%%M\usage.json" >nul
)

echo.
echo ============================================================
echo 迁移完成。请注意：
echo  1. .venv 虚拟环境不搬家：请到新文件夹重新双击 安装.bat；
echo  2. 打开新文件夹的 start.bat 确认项目、素材都在之后，
echo     再手动删除旧的 -V27 文件夹；
echo  3. 本脚本可重复执行（已存在的同名文件会被旧数据覆盖为准，
echo     程序代码文件不在复制范围内）。
echo ============================================================
pause
