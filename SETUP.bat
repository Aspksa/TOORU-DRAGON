@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
echo TOORU DRAGON 0.0.0 - подготовка переносимого Python
if exist "python\python.exe" goto verify_existing
if exist "python" goto partial
where curl.exe >nul 2>nul
if errorlevel 1 goto missing_tools
where tar.exe >nul 2>nul
if errorlevel 1 goto missing_tools
where certutil.exe >nul 2>nul
if errorlevel 1 goto missing_tools
set "TOORU_STAGE=%CD%\.setup-%RANDOM%-%RANDOM%"
mkdir "%TOORU_STAGE%\runtime"
if errorlevel 1 goto failed
echo Скачивание Python 3.13.15 для Windows x64, около 11 МБ...
curl.exe --fail --location --retry 2 --connect-timeout 20 --max-time 600 --proto =https --tlsv1.2 --progress-bar "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip" --output "%TOORU_STAGE%\python.zip"
if errorlevel 1 goto failed
certutil.exe -hashfile "%TOORU_STAGE%\python.zip" SHA256 > "%TOORU_STAGE%\hash.txt"
if errorlevel 1 goto failed
findstr /i /c:"d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf" "%TOORU_STAGE%\hash.txt" >nul
if errorlevel 1 goto hash_error
echo Распаковка и проверка Python...
tar.exe -xf "%TOORU_STAGE%\python.zip" -C "%TOORU_STAGE%\runtime"
if errorlevel 1 goto failed
> "%TOORU_STAGE%\runtime\python313._pth" echo python313.zip
>> "%TOORU_STAGE%\runtime\python313._pth" echo .
>> "%TOORU_STAGE%\runtime\python313._pth" echo ..
"%TOORU_STAGE%\runtime\python.exe" -X utf8 -c "import sqlite3,ssl,http.server,webbrowser; print('Python и SQLite готовы')"
if errorlevel 1 goto failed
if exist "python" goto failed
move "%TOORU_STAGE%\runtime" "python" >nul
if errorlevel 1 goto failed
rmdir /s /q "%TOORU_STAGE%"
goto success
:verify_existing
"python\python.exe" -X utf8 -c "import sqlite3,ssl,http.server,webbrowser; print('Python и SQLite готовы')"
if errorlevel 1 goto failed
:success
echo Готово. Запустите StartTooruDragon.bat.
if /i not "%~1"=="--quiet" pause
popd
exit /b 0
:hash_error
echo Контрольная сумма не совпала. Установка остановлена.
goto failed
:partial
echo Папка python существует, но python.exe отсутствует.
echo Переименуйте незавершённую папку python и повторите запуск.
goto failed
:missing_tools
echo Нужны встроенные средства Windows 10: curl.exe, tar.exe и certutil.exe.
echo Поддерживаются Windows 10 64-bit и Windows 11 64-bit.
:failed
echo Ошибка установки. Проверьте интернет, свободное место и сообщения выше.
if defined TOORU_STAGE if exist "%TOORU_STAGE%" rmdir /s /q "%TOORU_STAGE%"
if /i not "%~1"=="--quiet" pause
popd
exit /b 1
