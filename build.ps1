param(
    [string]$Name = "AudioSeparator"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:/Users/Administrator/AppData/Local/Programs/Python/Python313/python.exe"

Push-Location $Root
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python -m PyInstaller --noconfirm --clean --windowed --name $Name --collect-all numpy --collect-all demucs --collect-all torch --collect-all torchaudio app.py
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction

    if ($exitCode -ne 0) {
        throw "PyInstaller failed with exit code $exitCode"
    }

    Write-Host "Build completed. Output: dist/$Name/$Name.exe"
}
finally {
    Pop-Location
}
