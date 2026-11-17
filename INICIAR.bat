@echo off
cd /d "%~dp0"
echo ==========================================
echo       SCP - Sistema de Contas a Pagar
echo ==========================================
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale Python 3.10+ e marque "Add Python to PATH".
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Falha ao instalar dependencias.
  pause
  exit /b 1
)
set SCP_SECRET_KEY=troque-esta-chave-antes-de-usar-em-ambiente-compartilhado
python app.py
pause
