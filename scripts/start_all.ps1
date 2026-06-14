$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$runtimeDir = Join-Path $root ".run"
$backendPidFile = Join-Path $runtimeDir "backend.pid"
$frontendPidFile = Join-Path $runtimeDir "frontend.pid"

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -le 0 -or -not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        return
    }

    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
}

function Stop-PidFileProcess {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    $savedProcessId = 0
    if ([int]::TryParse((Get-Content $Path -Raw).Trim(), [ref]$savedProcessId)) {
        Stop-ProcessTree -ProcessId $savedProcessId
    }
    Remove-Item $Path -Force -ErrorAction SilentlyContinue
}

function Stop-PortProcess {
    param([int]$Port)

    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($ownerProcessId in $owners) {
        Stop-ProcessTree -ProcessId $ownerProcessId
    }
}

function Resolve-Python {
    $candidates = @(
        (Join-Path $backendDir ".venv\Scripts\python.exe"),
        (Join-Path $root ".python\Python312Embedded\python.exe"),
        (Join-Path $root ".python\Python312\python.exe")
    )

    $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pathPython) {
        $candidates += $pathPython.Source
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path $candidate)) {
            continue
        }

        try {
            $probe = Start-Process `
                -FilePath $candidate `
                -ArgumentList '-c "import fastapi, uvicorn, mysql.connector"' `
                -WindowStyle Hidden `
                -Wait `
                -PassThru
            if ($probe.ExitCode -eq 0) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    throw "No working Python environment with backend dependencies was found."
}

function Wait-ForUrl {
    param(
        [string]$Name,
        [string]$Url,
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "$Name exited during startup with code $($Process.ExitCode)."
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    throw "$Name did not become ready within $TimeoutSeconds seconds."
}

Write-Host "Stopping previous services..."
Stop-PidFileProcess -Path $backendPidFile
Stop-PidFileProcess -Path $frontendPidFile
Stop-PortProcess -Port 8010
Stop-PortProcess -Port 5173
Start-Sleep -Seconds 1

$python = Resolve-Python
$node = (Get-Command node.exe -ErrorAction Stop).Source
$viteScript = Join-Path $frontendDir "node_modules\vite\bin\vite.js"

if (-not (Test-Path $viteScript)) {
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    Write-Host "Installing frontend dependencies..."
    Push-Location $frontendDir
    try {
        & $npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

$backendRunner = Join-Path $PSScriptRoot "run_backend.py"
$backendOut = Join-Path $backendDir "backend.out.log"
$backendErr = Join-Path $backendDir "backend.err.log"
$frontendOut = Join-Path $frontendDir "frontend.out.log"
$frontendErr = Join-Path $frontendDir "frontend.err.log"

try {
    Write-Host "Starting backend..."
    $backendProcess = Start-Process `
        -FilePath $python `
        -ArgumentList ('"{0}"' -f $backendRunner) `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $backendPidFile -Value $backendProcess.Id
    Wait-ForUrl -Name "Backend" -Url "http://127.0.0.1:8010/api/health" -Process $backendProcess

    Write-Host "Starting frontend..."
    $frontendArguments = '"{0}" --host 127.0.0.1 --port 5173 --strictPort' -f $viteScript
    $frontendProcess = Start-Process `
        -FilePath $node `
        -ArgumentList $frontendArguments `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $frontendPidFile -Value $frontendProcess.Id
    Wait-ForUrl -Name "Frontend" -Url "http://127.0.0.1:5173" -Process $frontendProcess

    Write-Host "Services started successfully."
    Write-Host "Backend PID:  $($backendProcess.Id)"
    Write-Host "Frontend PID: $($frontendProcess.Id)"
} catch {
    Stop-PidFileProcess -Path $backendPidFile
    Stop-PidFileProcess -Path $frontendPidFile
    Stop-PortProcess -Port 8010
    Stop-PortProcess -Port 5173

    Write-Error $_
    Write-Host "Backend log:  $backendErr"
    Write-Host "Frontend log: $frontendErr"
    exit 1
}
