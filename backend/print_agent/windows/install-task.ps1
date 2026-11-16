<#
.SYNOPSIS
  Instala el agente de impresión como tarea programada de Windows (WIN-003B).

.DESCRIPTION
  Una sola vez, en PowerShell DE ADMINISTRADOR, después de dejar config.json (con el
  token del panel) en la carpeta de destino. Con -WhatIf sólo enseña lo que haría.

  Lo que hace:
    1. Crea la carpeta de destino y copia agent.py.
    2. Deja la carpeta sólo para SYSTEM, Administradores y la cuenta del agente
       (el token de config.json no lo lee nadie más).
    3. Comprueba el agente con --check (sin imprimir y sin recoger trabajos).
    4. Registra la tarea: arranca con el equipo, con la cuenta del agente y SIN
       privilegios elevados, una sola instancia, reinicio si se cae, sin límite de
       tiempo y también con batería.

  No abre puertos ni toca el cortafuegos: el agente sólo hace HTTPS de salida.

.EXAMPLE
  .\install-task.ps1 -WhatIf
  .\install-task.ps1 -AgentUser "$env:COMPUTERNAME\caja"
#>
[CmdletBinding()]
param(
    [string]$AgentUser = "$env:USERDOMAIN\$env:USERNAME",
    [string]$Python = '',
    [string]$Target = (Join-Path $env:ProgramData 'BlackDogStore\PrintAgent'),
    [string]$TaskName = 'BlackDogStore Print Agent',
    [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'

if (-not $Python) {
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $found) { throw 'No se encontró Python. Indique -Python <ruta a pythonw.exe>.' }
    $Python = Join-Path (Split-Path $found.Source) 'pythonw.exe'
}
if (-not (Test-Path $Python)) { throw "No existe $Python" }
$source = Join-Path (Split-Path $PSScriptRoot) 'agent.py'
if (-not (Test-Path $source)) { throw "No se encontró agent.py junto a este script ($source)." }
$config = Join-Path $Target 'config.json'
$agent = Join-Path $Target 'agent.py'
$arguments = "-I `"$agent`" --config `"$config`""

$plan = @"
Carpeta:        $Target
Propietario:    Administradores (toda la carpeta, también si ya existía)
Permisos:       SYSTEM y Administradores: control total. ${AgentUser}: sólo leer el programa
                y config.json; modificar sólo data\ (diario, candado, registro). Sin herencia.
Programa:       $Python $arguments
Tarea:          $TaskName
Cuenta:         $AgentUser (S4U: sin contraseña guardada, sin privilegios elevados)
Arranque:       al iniciar el equipo (+30 s)
Una instancia:  sí (IgnoreNew), y además el candado del diario
Reinicio:       cada 1 min si se cae, hasta 999 veces
Batería:        arranca y sigue con batería
Puertos:        ninguno
"@
Write-Output $plan
if ($WhatIf) { Write-Output 'WhatIf: no se ha cambiado nada.'; return }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Ejecute este script en una PowerShell de administrador.' }

function Invoke-Icacls {
    & icacls.exe @args | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls falló: $($args -join ' ')" }
}

$parent = Split-Path $Target
$data = Join-Path $Target 'data'
New-Item -ItemType Directory -Force -Path $data | Out-Null
# S-1-5-18 = SYSTEM, S-1-5-32-544 = Administradores (independiente del idioma de Windows).
# PRIMERO el propietario: quien hubiera creado antes estas carpetas en ProgramData
# (cualquier usuario puede) conservaría como dueño el poder de cambiar sus permisos.
Invoke-Icacls $parent /setowner '*S-1-5-32-544' /T /C
# Carpeta padre: sólo administración; la cuenta del agente sólo puede atravesarla.
Invoke-Icacls $parent /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${AgentUser}:(RX)"
# Programa y config.json: la cuenta del agente los LEE, no los cambia.
Invoke-Icacls $Target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${AgentUser}:(OI)(CI)RX" /T
# data\: diario, candado y registro. Lo único que el agente escribe.
Invoke-Icacls $data /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${AgentUser}:(OI)(CI)M" /T
Copy-Item -Path $source -Destination $agent -Force
# Se comprueba lo que quedó, no lo que se pidió.
$admins = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')).Translate([Security.Principal.NTAccount]).Value
foreach ($item in @($parent, $Target, $data, $agent)) {
    if ((Get-Acl $item).Owner -ne $admins) { throw "El propietario de $item no es $admins." }
}
$open = 'S-1-1-0|S-1-5-11|S-1-5-32-545'   # Todos, Usuarios autenticados, Usuarios
foreach ($item in @($Target, $agent)) {
    $wide = (Get-Acl $item).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -match "^($open)$" }
    if ($wide) { throw "$item sigue siendo accesible para todos los usuarios." }
}

if (-not (Test-Path $config)) {
    throw "Falta $config. Cópielo (con el token del panel) y vuelva a ejecutar el script."
}
if ((Get-Content $config -Raw) -match 'PEGA_AQUI|NOMBRE_DE_LA|ID_DE_LA') {
    throw "$config todavía tiene los textos de ejemplo."
}
if ((Get-Content $config -Raw) -notmatch '"journal"\s*:\s*"data[\\/]') {
    throw "En $config, ""journal"" tiene que estar en data\ (por ejemplo ""data\\print-agent-journal.json"")."
}
# config.json se copió después de fijar permisos: se le aplican también.
Invoke-Icacls $config /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "${AgentUser}:RX"
Invoke-Icacls $config /setowner '*S-1-5-32-544'
# La comprobación corre con python.exe para ver su salida.
$console = Join-Path (Split-Path $Python) 'python.exe'
& $console -I $agent --config $config --check
if ($LASTEXITCODE -ne 0) { throw 'La comprobación del agente falló: corrija lo marcado como MAL.' }

$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $Target
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $AgentUser -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description 'Agente de impresión de Black Dog Store (WIN-003B).' `
    -Force | Out-Null
Write-Output "Tarea registrada. Arrancar ahora: Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Detener:  Stop-ScheduledTask -TaskName '$TaskName'"
Write-Output "Quitar:   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
