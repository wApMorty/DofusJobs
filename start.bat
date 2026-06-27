@echo off
title DofusJobs
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
  echo   3^) Relance ce fichier start.bat
  echo.
  pause
  exit /b 1
)

echo.
echo   DofusJobs demarre... laisse cette fenetre OUVERTE.
echo   Ton navigateur va s'ouvrir sur http://127.0.0.1:8000
echo   Pour arreter l'appli : ferme cette fenetre.
echo.

%PY% -m webapp.app --open

rem --- Si le serveur s'arrete sur une erreur, garde la fenetre ouverte ---
echo.
echo   L'appli s'est arretee.
pause
