@echo off
title Crossnote Markdown Server

cd /d "%~dp0"

echo =====================================
echo       CROSSNOTE MARKDOWN SERVER
echo =====================================
echo.
echo A iniciar Crossnote na porta 4567...
echo.

call npx crossnote@latest serve . --port 4567

echo.
echo O servidor Crossnote foi encerrado.
pause