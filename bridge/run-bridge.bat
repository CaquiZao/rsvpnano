@echo off
rem Starts the bridge for an unattended session: no console to close by accident,
rem and a log on disk because nothing is watching stdout.
rem
rem Launched by run-bridge.vbs, which is what the scheduled task actually runs.

cd /d "%~dp0"

set "LOGDIR=%LOCALAPPDATA%\handy-bridge"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem Keep one previous run, so a crash loop cannot bury the log that explains it.
if exist "%LOGDIR%\bridge.log" move /y "%LOGDIR%\bridge.log" "%LOGDIR%\bridge.prev.log" >nul 2>&1

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

rem Restart on exit: a bridge that dies at 3am should be up again by morning
rem rather than waiting for the next reboot.
:loop
".venv\Scripts\python.exe" -m handy_bridge --config config.toml >> "%LOGDIR%\bridge.log" 2>&1
echo [%date% %time%] bridge exited, restarting in 15s >> "%LOGDIR%\bridge.log"
timeout /t 15 /nobreak >nul
goto loop
