<#
.SYNOPSIS
  Construye el instalador .exe del agente de impresión (PRINT-SETUP-01) con IExpress (incluido en Windows).

.EXAMPLE
  .\build-installer.ps1 -Server https://mitienda.com -Output C:\temp\Instalar-Impresora.exe

  El .exe lleva: Instalar-Impresora.ps1, install-task.ps1, agent.py e instalador.json (sólo el
  servidor por defecto: ningún token ni dato de la tienda). Python se descarga al instalar, del
  sitio oficial, con su SHA-256 fijado y la firma de la PSF verificada. No se versiona el .exe.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Output
)
$ErrorActionPreference = 'Stop'
if ($Server -notmatch '^https://[A-Za-z0-9.\-]+(:\d+)?$') { throw 'El servidor tiene que ser una dirección https:// sin ruta.' }
$windows = $PSScriptRoot
$stage = Join-Path $env:TEMP ('bds-build-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $stage | Out-Null
try {
    Copy-Item (Join-Path $windows 'Instalar-Impresora.ps1') $stage
    Copy-Item (Join-Path $windows 'install-task.ps1') $stage
    Copy-Item (Join-Path (Split-Path $windows) 'agent.py') $stage
    [IO.File]::WriteAllText((Join-Path $stage 'instalador.json'), (@{ server = $Server.TrimEnd('/') } | ConvertTo-Json),
        (New-Object Text.UTF8Encoding($false)))
    $Output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $files = 'Instalar-Impresora.ps1', 'install-task.ps1', 'agent.py', 'instalador.json'
    $strings = (0..($files.Count - 1) | ForEach-Object { "FILE$_=`"$($files[$_])`"" }) -join "`r`n"
    $entries = (0..($files.Count - 1) | ForEach-Object { "%FILE$_%=" }) -join "`r`n"
    $sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$Output
FriendlyName=Instalar impresora termica
AppLaunched=powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File Instalar-Impresora.ps1
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[Strings]
$strings
[SourceFiles]
SourceFiles0=$stage\
[SourceFiles0]
$entries
"@
    $sedFile = Join-Path $stage 'instalador.sed'
    [IO.File]::WriteAllText($sedFile, $sed, [Text.Encoding]::ASCII)
    & "$env:SystemRoot\System32\iexpress.exe" /N /Q $sedFile | Out-Null
    for ($i = 0; $i -lt 60 -and -not (Test-Path $Output); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path $Output)) { throw 'IExpress no generó el instalador.' }
    Write-Output "Instalador: $Output ($([math]::Round((Get-Item $Output).Length / 1KB)) KB)"
} finally {
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}
