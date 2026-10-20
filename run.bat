@echo off
chcp 1251 > nul
title 40 Vova40 — Локальный сервер

echo.
echo =======================================
echo       40 VOVA40 — УЧЁТ СЕКОНД-ХЕНДА
echo =======================================
echo.

:: Определяем свой IP в локальной сети
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
    set IP=%%a
    goto :gotip
)
:gotip
set IP=%IP: =%

if "%IP%"=="" set IP=127.0.0.1

echo  Локальный IP: %IP%
echo  Порт: 5000
echo.
echo  📱 Открой на телефоне:
echo     http://%IP%:5000
echo.
echo  🔒 Закрыть окно — остановить сервер.
echo.

:: Проверяем .env
if not exist ".env" (
    echo  ⚠ Файл настроек .env не найден!
    echo     Скопируй .env.example в .env и заполни WEB_PASSWORD.
    echo     Сервер не запустится без настроек.
    echo.
    pause
    exit /b 1
)

:: Устанавливаем переменную PORT
set PORT=5000

:: Запускаем сервер
python app.py

pause