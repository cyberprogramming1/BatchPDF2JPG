@echo off
REM ===========================================================================
REM  Builds dist\PDF2JPG.exe  (single file, Poppler embedded)
REM  Run from the project folder.  Requires Python 3.10+ (64-bit) on PATH.
REM ===========================================================================
setlocal
cd /d "%~dp0"

if not exist "poppler\Library\bin\pdftoppm.exe" (
    echo [ERROR] poppler\Library\bin\pdftoppm.exe not found.
    echo         Copy Poppler's "Library" folder into the "poppler" folder first. See README.md step 2.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || (echo [ERROR] Could not create venv & exit /b 1)
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip || exit /b 1
python -m pip install -r requirements-dev.txt || exit /b 1

echo Running tests...
python -m pytest -q || (echo [ERROR] Tests failed - build aborted & exit /b 1)

echo Building executable...
pyinstaller --noconfirm --clean PDF2JPG.spec || exit /b 1

echo.
echo  DONE: dist\PDF2JPG.exe
endlocal
