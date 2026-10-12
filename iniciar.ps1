# Arranca el dashboard en segundo plano (sin ventana) y abre el navegador.
# Si ya está en marcha, solo abre el navegador.
# Con -WaitPid (lo usa el botón "Reiniciar dashboard") espera antes a que termine ese proceso.
param([int]$Port = 8765, [switch]$NoBrowser, [int]$WaitPid = 0)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$url = "http://127.0.0.1:$Port"
$python = Join-Path $root '.venv\Scripts\python.exe'
$data = Join-Path $root 'data'
$pidFile = Join-Path $data 'server.pid'
$log = Join-Path $data 'server.log'
$errLog = Join-Path $data 'server.err.log'

function Show-Error([string]$msg) {
    Add-Type -AssemblyName PresentationFramework
    [void][System.Windows.MessageBox]::Show($msg, 'Router Dashboard', 'OK', 'Error')
    exit 1
}

function Test-Up {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url | Out-Null; $true } catch { $false }
}

if ($WaitPid) {
    Wait-Process -Id $WaitPid -Timeout 30 -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(15)
    while ((Test-Up) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
}

if (-not (Test-Up)) {
    if (-not (Test-Path $python)) {
        Show-Error "No encuentro el entorno de Python en:`n$python`n`nCréalo con:`npython -m venv .venv`n.venv\Scripts\python -m pip install -r requirements.txt"
    }
    New-Item -ItemType Directory -Force $data | Out-Null
    $p = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $Port `
        -RedirectStandardOutput $log -RedirectStandardError $errLog
    Set-Content -Path $pidFile -Value $p.Id

    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Up)) {
        if ($p.HasExited) {
            $tail = (Get-Content $errLog -Tail 15 -ErrorAction SilentlyContinue) -join "`n"
            Show-Error "El dashboard no ha podido arrancar:`n`n$tail`n`nRegistro completo: $errLog"
        }
        if ((Get-Date) -gt $deadline) {
            Show-Error "El dashboard tarda demasiado en arrancar. Mira el registro:`n$errLog"
        }
        Start-Sleep -Milliseconds 500
    }
}

if (-not $NoBrowser) { Start-Process $url }
