@echo off
REM ==========================================================
REM  Gera o executavel pdf2ofx.exe (Windows) a partir do
REM  pdf2ofx.py, usando PyInstaller.
REM
REM  Requisitos:
REM   - Python 3.10+ instalado (marque "Add Python to PATH"
REM     na instalacao, se ainda nao tiver feito)
REM   - Este arquivo .bat e o pdf2ofx.py na MESMA pasta
REM ==========================================================

cd /d "%~dp0"

echo.
echo ===============================================
echo  PDF2OFX - Gerador de executavel (Windows)
echo ===============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale o Python em https://www.python.org/downloads/
    echo e marque a opcao "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

echo [1/4] Atualizando pip...
python -m pip install --upgrade pip

echo.
echo [2/4] Instalando dependencias (pdfplumber, pyinstaller)...
python -m pip install pdfplumber pyinstaller

echo.
echo [3/4] Limpando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist pdf2ofx.spec del /q pdf2ofx.spec

echo.
echo [4/4] Gerando o executavel (isso pode levar alguns minutos)...
python -m PyInstaller --onefile --windowed --name pdf2ofx --collect-all pdfplumber pdf2ofx.py

echo.
if exist dist\pdf2ofx.exe (
    echo ===============================================
    echo  SUCESSO! O executavel foi criado em:
    echo  dist\pdf2ofx.exe
    echo ===============================================
) else (
    echo ===============================================
    echo  Algo deu errado. Veja as mensagens acima.
    echo ===============================================
)

pause
