$ErrorActionPreference = 'Stop'
$rootPath = $PSScriptRoot
$pythonPath = Join-Path $rootPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the environment and install requirements first. See README.md.'
}
# Only launch the installed application; never launch an OpenD installer.
$openDPath = Join-Path $env:APPDATA 'Futu_OpenD\Futu_OpenD.exe'
if (Test-Path -LiteralPath $openDPath) {
    $existing = Get-Process -Name 'Futu_OpenD' -ErrorAction SilentlyContinue
    if (-not $existing) {
        Start-Process -FilePath $openDPath -WorkingDirectory (Split-Path $openDPath) | Out-Null
    }
}
& $pythonPath (Join-Path $rootPath 'launch.py')
if ($LASTEXITCODE -ne 0) { throw 'Dashboard failed to start. See the application startup log.' }
