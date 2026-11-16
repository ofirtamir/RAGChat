@echo off
rem Starts the local indexing worker: downloads and parses the documents queued in the admin console.
cd /d "%~dp0.."
python scripts\index_worker.py run
pause
