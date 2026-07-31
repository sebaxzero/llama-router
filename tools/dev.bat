@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "INTERACTIVE=1"
if not "%~1"=="" set "INTERACTIVE=0"

call :find_system_python
call :select_python
if errorlevel 1 goto no_python
call :check_python
if errorlevel 1 goto bad_python

if "%INTERACTIVE%"=="0" goto cli

:menu
cls
echo ============================================================
echo  LLAMA ROUTER - HERRAMIENTAS DE DESARROLLO
echo ============================================================
echo  Python: %PYTHON_EXE% %PYTHON_ARG%
echo.
echo  [1] Ejecutar la aplicacion
echo  [2] Verificacion completa (compilar + tests)
echo  [3] Ejecutar todos los tests
echo  [4] Compilar fuentes Python
echo  [5] Generar capturas del README
echo  [6] Generar capturas personalizadas
echo  [7] Grabar video y GIF de demostracion
echo  [8] Regenerar iconos
echo  [9] Crear build release
echo  [D] Crear build debug
echo  [I] Instalar dependencias de desarrollo
echo  [H] Mostrar ayuda de consola
echo  [0] Salir
echo.
choice /c 123456789DIH0 /n /m "Selecciona una opcion: "
set "MENU_CHOICE=%errorlevel%"
if "%MENU_CHOICE%"=="13" goto end
call :menu_dispatch "%MENU_CHOICE%"
set "TASK_RC=%errorlevel%"
call :show_result "%TASK_RC%"
goto menu

:cli
call :cli_dispatch "%~1"
set "TASK_RC=%errorlevel%"
if not "%TASK_RC%"=="0" echo [ERROR] El comando termino con codigo %TASK_RC%.
goto end_with_result

:menu_dispatch
if "%~1"=="1" goto run_app
if "%~1"=="2" goto verify
if "%~1"=="3" goto tests
if "%~1"=="4" goto compile
if "%~1"=="5" goto screenshots
if "%~1"=="6" goto custom_screenshots
if "%~1"=="7" goto video
if "%~1"=="8" goto icon
if "%~1"=="9" goto build
if "%~1"=="10" goto build_debug
if "%~1"=="11" goto install_dependencies
if "%~1"=="12" goto help
exit /b 2

:cli_dispatch
if /i "%~1"=="app" goto run_app
if /i "%~1"=="verify" goto verify
if /i "%~1"=="tests" goto tests
if /i "%~1"=="compile" goto compile
if /i "%~1"=="screenshots" goto screenshots
if /i "%~1"=="custom-screenshots" goto custom_screenshots
if /i "%~1"=="video" goto video
if /i "%~1"=="icon" goto icon
if /i "%~1"=="build" goto build
if /i "%~1"=="build-debug" goto build_debug
if /i "%~1"=="install" goto install_dependencies
if /i "%~1"=="help" goto help
echo Comando desconocido: %~1
call :help
exit /b 2

:run_app
"%PYTHON_EXE%" %PYTHON_ARG% main.py
exit /b %errorlevel%

:verify
call :compile
if errorlevel 1 exit /b %errorlevel%
call :tests
exit /b %errorlevel%

:tests
"%PYTHON_EXE%" %PYTHON_ARG% -m unittest discover -s tools\tests -p "test_*.py"
exit /b %errorlevel%

:compile
"%PYTHON_EXE%" %PYTHON_ARG% -m compileall -q main.py llama_router tools\build.py tools\generate_icon.py tools\screenshots.py tools\tests
exit /b %errorlevel%

:screenshots
"%PYTHON_EXE%" %PYTHON_ARG% tools\screenshots.py
exit /b %errorlevel%

:custom_screenshots
echo.
echo Deja cada campo vacio para usar el valor entre corchetes.
set "SHOT_PAGES=dashboard"
set /p "SHOT_PAGES=Paginas separadas por espacios [dashboard]: "
set "SHOT_THEMES=midnight"
set /p "SHOT_THEMES=Temas separados por espacios [midnight]: "
set "SHOT_SIZES=1280x860"
set /p "SHOT_SIZES=Tamanos separados por espacios [1280x860]: "
set "SHOT_OUT=tools\_scratch"
set /p "SHOT_OUT=Carpeta de salida [tools\_scratch]: "
"%PYTHON_EXE%" %PYTHON_ARG% tools\screenshots.py --pages %SHOT_PAGES% --themes %SHOT_THEMES% --sizes %SHOT_SIZES% --out "%SHOT_OUT%"
exit /b %errorlevel%

:video
"%PYTHON_EXE%" %PYTHON_ARG% tools\screenshots.py --video --out _capture
exit /b %errorlevel%

:icon
"%PYTHON_EXE%" %PYTHON_ARG% tools\generate_icon.py
exit /b %errorlevel%

:build
"%PYTHON_EXE%" %PYTHON_ARG% tools\build.py
exit /b %errorlevel%

:build_debug
"%PYTHON_EXE%" %PYTHON_ARG% tools\build.py --debug
exit /b %errorlevel%

:install_dependencies
if not exist "tools\.venv\Scripts\python.exe" (
    if not defined SYSTEM_PYTHON_EXE (
        echo Se necesita Python 3.10 o posterior para crear tools\.venv.
        exit /b 1
    )
    "%SYSTEM_PYTHON_EXE%" %SYSTEM_PYTHON_ARG% -m venv tools\.venv
    if errorlevel 1 exit /b %errorlevel%
)
"tools\.venv\Scripts\python.exe" -m pip install -r tools\requirements.txt pyinstaller
if errorlevel 1 exit /b %errorlevel%
set "PYTHON_EXE=%CD%\tools\.venv\Scripts\python.exe"
set "PYTHON_ARG="
exit /b 0

:help
echo.
echo Uso: tools\dev.bat [comando]
echo.
echo Comandos:
echo   app                 Ejecutar la aplicacion
echo   verify              Compilar y ejecutar todos los tests
echo   tests               Ejecutar todos los tests
echo   compile             Compilar las fuentes Python
echo   screenshots         Regenerar las capturas del README
echo   custom-screenshots  Abrir el asistente de capturas
echo   video               Grabar el video y GIF en _capture
echo   icon                Regenerar los iconos de la aplicacion
echo   build               Crear el ejecutable release
echo   build-debug         Crear el ejecutable con consola
echo   install             Preparar tools\.venv con dependencias
echo   help                Mostrar esta ayuda
exit /b 0

:find_system_python
set "SYSTEM_PYTHON_EXE="
set "SYSTEM_PYTHON_ARG="
where py >nul 2>&1
if not errorlevel 1 (
    set "SYSTEM_PYTHON_EXE=py"
    set "SYSTEM_PYTHON_ARG=-3"
    exit /b 0
)
where python >nul 2>&1
if not errorlevel 1 (
    set "SYSTEM_PYTHON_EXE=python"
    set "SYSTEM_PYTHON_ARG="
    exit /b 0
)
exit /b 1

:select_python
set "PYTHON_EXE=%SYSTEM_PYTHON_EXE%"
set "PYTHON_ARG=%SYSTEM_PYTHON_ARG%"
if exist "tools\.venv\Scripts\python.exe" (
    "tools\.venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%CD%\tools\.venv\Scripts\python.exe"
        set "PYTHON_ARG="
    )
)
if not defined PYTHON_EXE exit /b 1
exit /b 0

:check_python
"%PYTHON_EXE%" %PYTHON_ARG% -c "import operator, sys; raise SystemExit(operator.lt(sys.version_info, (3, 10)))" >nul 2>&1
exit /b %errorlevel%

:show_result
echo.
if "%~1"=="0" (
    echo [OK] Tarea completada.
) else (
    echo [ERROR] La tarea termino con codigo %~1.
)
echo.
pause
exit /b 0

:no_python
echo No se encontro Python. Instala Python 3.10 o posterior y vuelve a intentarlo.
if "%INTERACTIVE%"=="1" pause
set "TASK_RC=1"
goto end_with_result

:bad_python
echo Se necesita Python 3.10 o posterior.
if "%INTERACTIVE%"=="1" pause
set "TASK_RC=1"
goto end_with_result

:end
endlocal
exit /b 0

:end_with_result
endlocal & exit /b %TASK_RC%
