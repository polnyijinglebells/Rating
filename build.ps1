$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $ProjectPython)) { $ProjectPython = "python" }

& $ProjectPython -m unittest discover -s tests -v
& $ProjectPython -m PyInstaller --clean build_windows.spec

$Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($Compiler) {
    & $Compiler.Source installer.iss
    Write-Host "Установщик создан в installer_output"
} else {
    Write-Host "EXE создан в dist. Для установщика установите Inno Setup 6 и повторите сборку."
}
