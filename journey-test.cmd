@echo off
REM 一键旅程测试：jsdom 真实渲染 J1-J8 + 契约/状态机套件
cd /d "%~dp0web"
call "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs" vitest run src/journey
set J=%ERRORLEVEL%
call "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs" vitest run src/selfcheck
set S=%ERRORLEVEL%
cd /d "%~dp0server"
".venv\Scripts\python.exe" -m pytest -q
set P=%ERRORLEVEL%
echo === JOURNEY J=%J% SELFCHECK-WEB S=%S% PYTEST P=%P% ===
if not "%J%%S%%P%"=="000" exit /b 1