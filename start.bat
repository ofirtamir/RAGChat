@echo off
echo Starting RAGChat...
echo.
echo [1/2] Starting FastAPI backend on port 8000...
start "RAGChat Backend" cmd /k "cd /d %~dp0 && python -m uvicorn backend.api:app --reload --port 8000"
echo.
echo [2/2] Starting Next.js frontend on port 3000...
start "RAGChat Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"
echo.
echo RAGChat is starting!
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:3000
echo.
pause
