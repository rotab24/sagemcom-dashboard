# Detiene el dashboard arrancado con iniciar.ps1.
param([int]$Port = 8765, [switch]$Quiet)

$root = $PSScriptRoot
$pidFile = Join-Path $root 'data\server.pid'
$ids = @()

# El python.exe del venv es un lanzador que crea otro proceso: se para el que escucha en el puerto y el lanzador.
$ids += Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess
if (Test-Path $pidFile) { $ids += [int](Get-Content $pidFile) }

$stopped = $false
foreach ($id in ($ids | Sort-Object -Unique)) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like 'python*') {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }
}
Remove-Item $pidFile -ErrorAction SilentlyContinue

if ($Quiet) { if ($stopped) { 'Detenido' } else { 'No estaba en marcha' }; exit }
Add-Type -AssemblyName PresentationFramework
$msg = if ($stopped) { 'Dashboard detenido.' } else { 'El dashboard no estaba en marcha.' }
[void][System.Windows.MessageBox]::Show($msg, 'Router Dashboard', 'OK', 'Information')
