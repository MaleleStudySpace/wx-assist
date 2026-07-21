@echo off
cd /d C:\Users\74062\Desktop\wx-assist
set PYTHONPATH=.
D:\Python313\python.exe -c "import sys, time; from src.web.server import start_web_server; t = start_web_server(); print('Server started' if t else 'Failed', flush=True); [time.sleep(1) for _ in iter(int,1)]"
