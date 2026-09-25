$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $ProjectPython)) { $ProjectPython = "python" }

& $ProjectPython -m unittest discover -s tests -v
& $ProjectPython -m PyInstaller --clean build_windows.spec

$Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $Compiler) {
    $KnownCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $KnownCompiler) { $Compiler = Get-Item $KnownCompiler }
}
if (-not $Compiler) {
    $KnownCompiler = "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    if (Test-Path $KnownCompiler) { $Compiler = Get-Item $KnownCompiler }
}
if (-not $Compiler) {
    $KnownCompiler = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path $KnownCompiler) { $Compiler = Get-Item $KnownCompiler }
}
if ($Compiler) {
    $CompilerPath = if ($Compiler.Source) { $Compiler.Source } else { $Compiler.FullName }
    & $CompilerPath installer.iss
    Write-Host "Installer created in installer_output"
} else {
    Write-Host "EXE created in dist. Install Inno Setup 6 and run the build again to create the installer."
}
