$ErrorActionPreference = 'Stop'

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $repository 'services\backend'
$buildEnvironment = Join-Path $backend '.venv-build'
$python = if ($env:PHOTOFLOW_BUILD_PYTHON) { $env:PHOTOFLOW_BUILD_PYTHON } else { 'python' }

$version = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '3.12') {
    throw 'PhotoFlow packaging requires Python 3.12. Set PHOTOFLOW_BUILD_PYTHON to a Python 3.12 executable.'
}

$venvPython = Join-Path $buildEnvironment 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $python -m venv $buildEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python packaging environment.' }
}
$venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $venvVersion.Trim() -ne '3.12') {
    throw 'The existing packaging virtual environment is not Python 3.12. Remove services\backend\.venv-build and retry.'
}

& $venvPython -m pip install -r (Join-Path $backend 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python backend packaging requirements.' }

$distribution = Join-Path $backend 'distribution'
$distOutput = Join-Path $backend 'dist'
$workOutput = Join-Path $backend 'build\pyinstaller'
$specOutput = Join-Path $backend 'build'
$migrationDirectory = Join-Path $backend 'photoflow_backend\migrations'
New-Item -ItemType Directory -Force -Path $distribution | Out-Null

Push-Location $backend
try {
    & $venvPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --name photoflow-backend `
        --collect-all PIL `
        --add-data "$migrationDirectory;photoflow_backend\migrations" `
        --distpath $distOutput `
        --workpath $workOutput `
        --specpath $specOutput `
        run_backend.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to package the PhotoFlow backend.' }
}
finally {
    Pop-Location
}

$backendExecutable = Join-Path $distOutput 'photoflow-backend.exe'
if (-not (Test-Path $backendExecutable)) { throw 'PyInstaller did not produce the backend executable.' }
Copy-Item -Force $backendExecutable (Join-Path $distribution 'photoflow-backend.exe')

$bundledTools = Join-Path $distribution 'bin'
if (Test-Path $bundledTools) {
    Remove-Item -LiteralPath $bundledTools -Recurse -Force
}

$providedTools = Join-Path $backend 'bin'
if (Test-Path $providedTools) {
    foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
        $source = Join-Path $providedTools $tool
        if (Test-Path $source -PathType Leaf) {
            New-Item -ItemType Directory -Force -Path $bundledTools | Out-Null
            Copy-Item -LiteralPath $source -Destination (Join-Path $bundledTools $tool) -Force
        }
    }
}

& $venvPython (Join-Path $repository 'scripts\smoke-packaged-backend.py') (Join-Path $distribution 'photoflow-backend.exe')
if ($LASTEXITCODE -ne 0) { throw 'Packaged backend integration smoke test failed.' }

Write-Host "Backend packaged to $distribution"
