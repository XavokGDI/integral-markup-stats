@echo off
cd /d "%~dp0"

REM Создаём виртуальное окружение при первом запуске
if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
    if errorlevel 1 (
        echo ОШИБКА: Python не найден. Установите Python и добавьте его в PATH.
        pause
        exit /b 1
    )
)

REM Активируем окружение
call venv\Scripts\activate.bat

REM Устанавливаем зависимости при первом запуске
if not exist venv\Scripts\streamlit.exe (
    echo Устанавливаю зависимости...
    python -m pip install -r requirements.txt
)

REM Запускаем приложение
streamlit run app.py
pause
