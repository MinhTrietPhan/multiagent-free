@echo off
cd /d D:\Trietdeptrai\agent-hub
call conda activate agent-hub
set PYTHONPATH=src
python -m hub.cli
pause