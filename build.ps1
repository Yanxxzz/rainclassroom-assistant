$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$rcPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $rcPython -m pip install -r requirements.txt 'pyinstaller==6.22.3'
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $rcPython -m unittest discover -s tests
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $rcPython -m PyInstaller --noconfirm --onefile --windowed --name RainDesk RainDesk.py
exit $LASTEXITCODE
