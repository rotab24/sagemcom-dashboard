# Crea los accesos directos "Router Dashboard" y "Detener Router Dashboard" en el escritorio y el menú Inicio.
# Con -Autostart, además arranca el dashboard (sin abrir el navegador) al iniciar sesión en Windows.
# Uso: powershell -ExecutionPolicy Bypass -File crear-accesos.ps1 [-Autostart]
param([switch]$Autostart)

$root = $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function New-Shortcut([string]$path, [string]$script, [string]$extra, [string]$icon, [string]$desc) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $ps
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $root $script)`" $extra".Trim()
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $icon
    $lnk.Description = $desc
    $lnk.WindowStyle = 7  # minimizada: evita el parpadeo de la consola
    $lnk.Save()
    Write-Output "Creado: $path"
}

$startIcon = "$env:SystemRoot\System32\imageres.dll,20"
$stopIcon = "$env:SystemRoot\System32\imageres.dll,93"
$desktop = [Environment]::GetFolderPath('Desktop')
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Router Dashboard'
New-Item -ItemType Directory -Force $menu | Out-Null

foreach ($dir in @($desktop, $menu)) {
    New-Shortcut (Join-Path $dir 'Router Dashboard.lnk') 'iniciar.ps1' '' $startIcon 'Abre el dashboard del router'
}
New-Shortcut (Join-Path $menu 'Detener Router Dashboard.lnk') 'detener.ps1' '' $stopIcon 'Detiene el dashboard del router'

$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Router Dashboard.lnk'
if ($Autostart) {
    New-Shortcut $startup 'iniciar.ps1' '-NoBrowser' $startIcon 'Arranca el dashboard del router al iniciar sesión'
} elseif (Test-Path $startup) {
    Remove-Item $startup
    Write-Output "Quitado el arranque automático: $startup"
}
