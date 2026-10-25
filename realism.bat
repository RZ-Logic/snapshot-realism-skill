@echo off
setlocal DisableDelayedExpansion
rem Snapshot Realism launcher for Windows.
rem
rem Drag one or more images onto this file. For each image it asks:
rem   origin: [C]amera photo, AI-[E]dited camera photo or [G]enerated image
rem   light:  [D]ay, [I]ndoor or [N]ight (the scene's actual light level)
rem   capture strength: [0] Off, [L]ight, [N]ormal or [S]trong
rem then runs realism.py, which writes <name>_real.jpg (or <name>_real-2.jpg
rem and so on, if that exists) and the before/after <name>_real_compare.jpg
rem next to the image. It never replaces an existing file.
rem Needs Python 3.9+ with numpy and Pillow: python -m pip install numpy pillow
rem From a cmd prompt: realism.bat "C:\photos\IMG 0001.jpg" "C:\photos\b.png"
rem
rem How it keeps file names intact:
rem - Delayed expansion stays off while paths are handled; with it on, "!" and
rem   "^" vanish from names.
rem - Each name travels in SR_FILE, never as a CALL argument: CALL expands
rem   percent signs a second time and doubles carets.
rem - Explorer quotes a dropped name only when it has a space, so a name such
rem   as a&b.png reaches cmd unquoted and cmd splits it at the "&". After a drop
rem   this file ends with a full EXIT, so cmd never runs the text after the "&".
rem - Python is found once, as the full path of the real python.exe, and every
rem   later step runs that file. Running a python.bat or python.cmd shim (such
rem   as pyenv-win's) directly from a batch file would hand over control and
rem   never come back, so the launcher would end silently.

rem Started by Windows to run this file (a drop, a double-click, or a program
rem such as PowerShell opening it)? Then the command line holds 'cmd.exe /c ""'.
rem Only then does it pause at the end and end with a full EXIT.
set "SR_DROPPED="
setlocal EnableDelayedExpansion
set "SR_A=!cmdcmdline!"
set "SR_B=!SR_A:cmd.exe /c ""=!"
if not "!SR_A!"=="!SR_B!" set "SR_DROPPED=1"
endlocal & set "SR_DROPPED=%SR_DROPPED%"

rem %%0 changes after SHIFT, so resolve the script path first.
set "SR_SCRIPT=%~dp0skill\snapshot-realism\scripts\realism.py"
set "SR_EXIT=0"
set /a SR_OK=0, SR_REFUSED=0, SR_FAIL=0, SR_SKIP=0
set "SR_PREV_FULL="

if not exist "%SR_SCRIPT%" goto :no_script
rem FOR /F runs the command in a child cmd, so a .bat or .cmd shim returns
rem here. The child also runs any AutoRun command, which can read stdin and
rem eat the answers to the prompts below, hence the <nul. A line counts only
rem if it names an existing file, so a stub's message is ignored.
set "SR_PY="
(for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do if exist "%%~P" if not exist "%%~P\*" set "SR_PY=%%~P") <nul
if not defined SR_PY (for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if exist "%%~P" if not exist "%%~P\*" set "SR_PY=%%~P") <nul
if not defined SR_PY goto :no_python
"%SR_PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python
"%SR_PY%" -c "import numpy, PIL" >nul 2>nul
if errorlevel 1 goto :no_packages
if "%~1"=="" goto :no_files

echo Origin: C = a genuine camera photo
echo         E = a camera photo with AI-generated parts, such as inpainting
echo         G = made by an image model, including a scene rebuilt from a reference photo
echo Light:  the scene's actual light level: D = day, I = indoor, N = night
echo Capture strength: 0 = off (best for your own camera photos), L = light (the default for generated images), N = normal, S = strong

:next
if "%~1"=="" goto :summary
set "SR_FILE=%~f1"
set "SR_RAW=%~1"
rem When Windows started this file (a drop), a name it split at , ; = arrives
rem as a full path (missing, or a folder or file that happens to match the
rem first piece), then relative pieces that %~f1 would resolve against the
rem current folder, so they could match an unrelated file. Explorer passes
rem only full paths, so in a drop every relative argument after a full path
rem (or after another piece) is skipped. Plain relative names, and every
rem argument typed at a prompt, are processed as given.
set "SR_FRAGMENT="
if defined SR_DROPPED if defined SR_PREV_FULL if not "%SR_RAW:~1,1%"==":" if not "%SR_RAW:~0,2%"=="\\" set "SR_FRAGMENT=1"
set "SR_PREV_FULL="
if "%SR_RAW:~1,1%"==":" set "SR_PREV_FULL=1"
if "%SR_RAW:~0,2%"=="\\" set "SR_PREV_FULL=1"
if defined SR_FRAGMENT (call :one_fragment) else (call :one)
shift
goto :next

:one_fragment
echo.
echo [skip] Part of a split name: "%SR_RAW%"
echo        Windows splits a dropped name that has no space and contains , ; = ^& ^^ or %%.
echo        Rename the file, or run realism.py on it from a prompt.
set /a SR_SKIP+=1
set "SR_PREV_FULL=1"
exit /b

:one
echo.
if exist "%SR_FILE%\*" goto :one_folder
if exist "%SR_FILE%" goto :one_found
echo [skip] Not found: "%SR_FILE%"
echo        Windows splits or changes a dropped name that has no space and contains
echo        , ; = ^& ^^ or %%. Rename the file, or run realism.py on it from a prompt.
set /a SR_SKIP+=1
exit /b

:one_folder
echo [skip] Folder, not an image: "%SR_FILE%"
set /a SR_SKIP+=1
exit /b

:one_found
echo File: "%SR_FILE%"
choice /c CEG /n /m "Origin: [C]amera photo, AI-[E]dited camera photo or [G]enerated image? "
set "SR_RC=%errorlevel%"
set "SR_ORIGIN="
if "%SR_RC%"=="1" set "SR_ORIGIN=camera"
if "%SR_RC%"=="2" set "SR_ORIGIN=edited"
if "%SR_RC%"=="3" set "SR_ORIGIN=generated"
if not defined SR_ORIGIN goto :one_no_answer
choice /c DIN /n /m "Light: [D]ay, [I]ndoor or [N]ight? "
set "SR_RC=%errorlevel%"
set "SR_PRESET="
if "%SR_RC%"=="1" set "SR_PRESET=day"
if "%SR_RC%"=="2" set "SR_PRESET=indoor"
if "%SR_RC%"=="3" set "SR_PRESET=night"
if not defined SR_PRESET goto :one_no_answer
choice /c 0LNS /n /m "Capture strength: [0] Off, [L]ight, [N]ormal or [S]trong? "
set "SR_RC=%errorlevel%"
set "SR_STRENGTH="
if "%SR_RC%"=="1" set "SR_STRENGTH=0"
if "%SR_RC%"=="2" set "SR_STRENGTH=0.5"
if "%SR_RC%"=="3" set "SR_STRENGTH=1"
if "%SR_RC%"=="4" set "SR_STRENGTH=2"
if not defined SR_STRENGTH goto :one_no_answer
"%SR_PY%" "%SR_SCRIPT%" "%SR_FILE%" --origin %SR_ORIGIN% --preset %SR_PRESET% --capture-strength %SR_STRENGTH% --compare
set "SR_RC=%errorlevel%"
if "%SR_RC%"=="0" goto :one_written
if "%SR_RC%"=="2" goto :one_refused
echo [failed] Nothing written for "%SR_FILE%"; see the error above.
set /a SR_FAIL+=1
exit /b

:one_written
set /a SR_OK+=1
exit /b

:one_refused
echo [refused] Nothing written for "%SR_FILE%"; realism.py gives the reason above.
echo           If it says the metadata marks the image as AI-made, run it again and choose E or G.
set /a SR_REFUSED+=1
exit /b

:one_no_answer
echo.
echo [skip] No answer; nothing written for "%SR_FILE%"
set /a SR_SKIP+=1
exit /b

:no_script
echo Cannot find "%SR_SCRIPT%".
echo Keep realism.bat in the folder that holds the skill folder.
set "SR_EXIT=1"
goto :end

:no_python
echo No working Python found: neither "python" nor "py -3" ran. The Microsoft Store shortcut does not count.
echo Install Python 3.9 or newer from python.org with "Add python.exe to PATH",
echo then run: python -m pip install numpy pillow
set "SR_EXIT=1"
goto :end

:old_python
echo The Python found is older than 3.9: "%SR_PY%"
echo Install Python 3.9 or newer from python.org, then run: python -m pip install numpy pillow
set "SR_EXIT=1"
goto :end

:no_packages
echo numpy or Pillow is missing for "%SR_PY%".
echo Run: "%SR_PY%" -m pip install numpy pillow
set "SR_EXIT=1"
goto :end

:no_files
echo Drag one or more images onto realism.bat, or run: realism.bat "C:\photos\photo.jpg"
set "SR_EXIT=2"
goto :end

:summary
echo.
echo Done: %SR_OK% written, %SR_REFUSED% refused, %SR_FAIL% failed, %SR_SKIP% skipped.
if %SR_FAIL% gtr 0 set "SR_EXIT=1"
if %SR_REFUSED% gtr 0 set "SR_EXIT=1"

:end
if not defined SR_DROPPED exit /b %SR_EXIT%
pause
exit %SR_EXIT%
