@echo off
setlocal
cd /d "%~dp0"

if not defined SUMO_HOME (
    if exist "C:\Program Files (x86)\SUMO\bin\sumo-gui.exe" (
        set "SUMO_HOME=C:\Program Files (x86)\SUMO"
    ) else if exist "C:\Program Files\SUMO\bin\sumo-gui.exe" (
        set "SUMO_HOME=C:\Program Files\SUMO"
    ) else (
        echo [ERRO] SUMO_HOME nao encontrado. Verifica a instalacao do SUMO.
        pause
        exit /b 1
    )
)
if "%SUMO_HOME:~-1%"=="\" set "SUMO_HOME=%SUMO_HOME:~0,-1%"
set "PATH=%SUMO_HOME%\bin;%PATH%"
set "PYTHONPATH=%SUMO_HOME%\tools;%PYTHONPATH%"

echo =====================================================
echo   MobilisTransit - Linha 9 no SUMO GUI (com transito)
echo =====================================================
echo.
echo Escolhe a densidade de transito:
echo   [1] Pouco transito   (1 carro a cada 1.5s)
echo   [2] Transito medio   (1 carro a cada 0.5s) [Padrao]
echo   [3] Hora de ponta    (1 carro a cada 0.25s)
echo.
set "CHOICE="
set /p "CHOICE=Opcao [1/2/3] (Enter = 2): "

set "LEVEL=medium"
set "PERIOD=0.5"
set "SEED=22"
if "%CHOICE%"=="1" (
    set "LEVEL=light"
    set "PERIOD=1.5"
    set "SEED=11"
)
if "%CHOICE%"=="3" (
    set "LEVEL=heavy"
    set "PERIOD=0.25"
    set "SEED=33"
)

if not exist "generated_traffic" mkdir "generated_traffic"
set "TRIPS_FILE=generated_traffic\demo_trips_%LEVEL%.trips.xml"
set "ROUTE_FILE=generated_traffic\demo_traffic_%LEVEL%.rou.xml"

if not exist "%ROUTE_FILE%" (
    echo.
    echo A gerar transito [%LEVEL%] com randomTrips.py (so demora na 1a vez^)...
    python "%SUMO_HOME%\tools\randomTrips.py" -n leiria.net.xml.gz --vehicle-class passenger -b 25700 -e 29000 -p %PERIOD% --seed %SEED% --fringe-factor 5 -o "%TRIPS_FILE%" -r "%ROUTE_FILE%"
    if errorlevel 1 (
        echo.
        echo [ERRO] Falhou a geracao do transito.
        pause
        exit /b 1
    )
) else (
    echo.
    echo A reutilizar ficheiro ja gerado: %ROUTE_FILE%
)

set "ADD_FILES=pt_vtypes.xml,gtfs_pt_stops.add.xml,gtfs_pt_vehicles_L9.add.xml"
if exist "deprecated\2026-11-21-19-40-29\osm.poly.xml.gz" (
    set "ADD_FILES=%ADD_FILES%,deprecated\2026-11-21-19-40-29\osm.poly.xml.gz"
)

set "VIEW_OPT="
if exist "deprecated\2026-11-21-19-40-29\osm.view.xml" (
    set "VIEW_OPT=--gui-settings-file deprecated\2026-11-21-19-40-29\osm.view.xml"
)

echo.
echo A abrir SUMO GUI... (O 1o autocarro da Linha 9 parte aos 25790s - ~2s apos Play)
if "%DRY_RUN%"=="1" (
    echo DRY_RUN CMD: "%SUMO_HOME%\bin\sumo-gui.exe" -n leiria.net.xml.gz -a "%ADD_FILES%" -r "%ROUTE_FILE%" --begin 25700 --end 72000 --ignore-route-errors %VIEW_OPT%
    exit /b 0
)
start "" "%SUMO_HOME%\bin\sumo-gui.exe" -n leiria.net.xml.gz -a "%ADD_FILES%" -r "%ROUTE_FILE%" --begin 25700 --end 72000 --ignore-route-errors %VIEW_OPT%

endlocal
