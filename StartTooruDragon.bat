@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title TOORU DRAGON 0.0.0
pushd "%~dp0"
if errorlevel 1 goto path_error
if exist "python\python.exe" goto run
call "%~dp0SETUP.bat" --quiet
if errorlevel 1 goto failed
:run
"%~dp0python\python.exe" -X utf8 "%~dp0app.py" %*
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo.
echo Не удалось запустить TOORU. Сообщение об ошибке показано выше.
echo Если ошибка связана с Python, проверьте папку python и выполните SETUP.bat.
pause
popd
exit /b 1
:path_error
echo Не удалось открыть папку проекта. Распакуйте ZIP перед запуском.
pause
exit /b 1
