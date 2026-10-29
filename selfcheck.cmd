@echo off
rem One-shot selfcheck: server pytest + web tsc + web vitest. Exit code = first failure.
setlocal
set RC=0
set ROOT=%~dp0

echo === [1/3] server pytest ===
pushd "%ROOT%server"
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 set RC=1
popd

echo === [2/3] web tsc -b ===
pushd "%ROOT%web"
"C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs" exec tsc -b
if errorlevel 1 set RC=1

echo === [3/3] web vitest ===
"C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" "C:\Users\YMS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs" run selfcheck
if errorlevel 1 set RC=1
popd

if "%RC%"=="0" (echo === SELFCHECK PASS ===) else (echo === SELFCHECK FAIL rc=%RC% ===)
exit /b %RC%
