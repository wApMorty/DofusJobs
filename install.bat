@echo off
title DofusJobs - Initialisation des donnees
cd /d "%~dp0"

rem --- Trouve un lanceur Python (py en priorite, puis python) ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
  echo.
  echo   [X] Python n'est pas installe.
  echo.
  echo   1^) Telecharge-le sur https://www.python.org/downloads/
  echo   2^) Pendant l'installation, COCHE la case "Add Python to PATH"
  echo   3^) Relance ce fichier install.bat
  echo.
  pause
  exit /b 1
)

if not exist "data" mkdir "data"

echo.
echo   Construction des donnees depuis l'API DofusDB...
echo   (necessite une connexion Internet, ca prend 1 a 2 minutes)
echo.

%PY% scripts\build_dofusmap_counts.py
if errorlevel 1 (
  echo.
  echo   [X] Echec du telechargement des positions de ressources.
  echo       Verifie ta connexion Internet, puis relance install.bat.
  echo.
  pause
  exit /b 1
)

%PY% scripts\build_dofusdb_dataset.py
if errorlevel 1 (
  echo.
  echo   [X] Echec de la construction des donnees.
  echo       Verifie ta connexion Internet, puis relance install.bat.
  echo.
  pause
  exit /b 1
)

echo.
echo   [OK] Donnees pretes. Tu peux maintenant lancer start.bat.
echo.
pause
