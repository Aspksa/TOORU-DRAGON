@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title TOORU DRAGON - обновление
pushd "%~dp0"
if errorlevel 1 goto path_error
if exist "python\python.exe" goto run
call "%~dp0SETUP.bat" --quiet
if errorlevel 1 goto failed
:run
echo ==========================================
echo       TOORU DRAGON - ОБНОВЛЕНИЕ
echo ==========================================
echo.
echo Перед ручным обновлением закройте запущенную TOORU.
echo Данные, Python и настройки будут сохранены.
echo.
"%~dp0python\python.exe" -X utf8 "%~dp0updater.py" --root "%~dp0" --apply --restart
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo.
echo Обновление не выполнено. Подробности: data\logs\update.log
pause
popd
exit /b 1
:path_error
echo Не удалось открыть папку TOORU.
pause
exit /b 1
