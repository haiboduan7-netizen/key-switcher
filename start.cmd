@echo off
rem Key Switch 启动器：启动 watchdog 守护（网关掉线 20 秒内自动拉起），然后打开页面
rem 可选：把本文件快捷方式放入 shell:startup 实现开机自启
set "PYW=pythonw"
where %PYW% >nul 2>nul || set "PYW=python"
start "" %PYW% "%~dp0watchdog.py"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:18791"
exit
