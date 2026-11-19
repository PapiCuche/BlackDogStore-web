<#
.SYNOPSIS
  Instalador con ventana del agente de impresión (PRINT-SETUP-01): se pega el token y listo.

.DESCRIPTION
  Hace en esta PC todo lo que antes se hacía a mano (WIN-003D.2):
    1. Python 3.12 oficial («embeddable», firmado por la PSF; SHA-256 fijado) para todos los usuarios.
    2. Cuenta estándar dedicada «bds-print» (no administradora, contraseña aleatoria que nadie
       conoce) con el derecho «Iniciar sesión como proceso por lotes».
    3. Las colas de Windows elegidas (tickets y, si la hay, etiquetas) en «imprimir después de la
       última página».
    4. config.json con el token (pedido oculto; nunca se muestra ni se registra).
    5. install-task.ps1: permisos mínimos, --check y la tarea programada.
    6. --check con la identidad de «bds-print», arranque, un ticket de prueba y, si hay impresora de
       etiquetas (LABEL-04), una etiqueta de prueba del tamaño del rollo que dice el panel.
  Vuelve a ejecutarse para cambiar el token o la impresora: conserva el diario (data\).
  No abre puertos ni toca el cortafuegos.
#>
[CmdletBinding()]
param([switch]$Staged)
$ErrorActionPreference = 'Stop'

# --- Copia de trabajo y elevación -------------------------------------------------------
# IExpress borra sus archivos al terminar el primer proceso: antes de pedir permisos de
# administrador, los archivos se copian a una carpeta propia y se eleva esa copia.
$here = $PSScriptRoot
if (-not $Staged) {
    $stage = Join-Path $env:TEMP ('BlackDogStore-instalador-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force (Join-Path $stage 'print_agent\windows') | Out-Null
    $agentSource = @((Join-Path $here 'agent.py'), (Join-Path (Split-Path $here) 'agent.py')) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $agentSource) { throw 'No se encontró agent.py junto al instalador.' }
    Copy-Item $agentSource (Join-Path $stage 'print_agent\agent.py')
    foreach ($name in 'Instalar-Impresora.ps1', 'install-task.ps1', 'instalador.json') {
        $file = Join-Path $here $name
        if (Test-Path $file) { Copy-Item $file (Join-Path $stage "print_agent\windows\$name") }
    }
    $script = Join-Path $stage 'print_agent\windows\Instalar-Impresora.ps1'
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', "`"$script`"", '-Staged')
    return
}

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$settingsFile = Join-Path $here 'instalador.json'
$settings = if (Test-Path $settingsFile) { Get-Content $settingsFile -Raw | ConvertFrom-Json } else { $null }
$DefaultServer = if ($settings -and $settings.server) { $settings.server } else { '' }
$PythonVersion = '3.12.10'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonSha256 = '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'
$PythonDir = Join-Path $env:ProgramFiles 'BlackDogStore\Python312'
$Target = Join-Path $env:ProgramData 'BlackDogStore\PrintAgent'
$TaskName = 'BlackDogStore Print Agent'
$Account = 'bds-print'
$AgentUser = "$env:COMPUTERNAME\$Account"
$LogFile = Join-Path $env:ProgramData 'BlackDogStore\instalacion.log'
$UserAgent = 'BlackDogStore-PrintAgent/instalador'

# --- Ventana --------------------------------------------------------------------------
$form = New-Object Windows.Forms.Form
$form.Text = 'Instalar impresora térmica'
$form.ClientSize = New-Object Drawing.Size(560, 770)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Segoe UI', 10)

function Add-Label($text, $y, [switch]$Bold) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $text; $label.Location = New-Object Drawing.Point(20, $y); $label.AutoSize = $true
    if ($Bold) { $label.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold) }
    $form.Controls.Add($label); return $label
}

Add-Label 'Servidor de la tienda' 15 -Bold | Out-Null
$serverBox = New-Object Windows.Forms.TextBox
$serverBox.Location = New-Object Drawing.Point(20, 38); $serverBox.Width = 520; $serverBox.Text = $DefaultServer
$form.Controls.Add($serverBox)

Add-Label 'Token del agente (panel › Impresoras › crear agente)' 75 -Bold | Out-Null
$tokenBox = New-Object Windows.Forms.TextBox
$tokenBox.Location = New-Object Drawing.Point(20, 98); $tokenBox.Width = 400; $tokenBox.UseSystemPasswordChar = $true
$form.Controls.Add($tokenBox)
$checkButton = New-Object Windows.Forms.Button
$checkButton.Text = 'Comprobar'; $checkButton.Location = New-Object Drawing.Point(430, 96); $checkButton.Width = 110
$form.Controls.Add($checkButton)
$whoLabel = Add-Label '' 130

Add-Label 'TICKETS — impresora del panel' 160 -Bold | Out-Null
$panelBox = New-Object Windows.Forms.ComboBox
$panelBox.Location = New-Object Drawing.Point(20, 183); $panelBox.Width = 520; $panelBox.DropDownStyle = 'DropDown'
$form.Controls.Add($panelBox)
$panelHint = Add-Label 'Pulse «Comprobar». Si no aparece la lista, escriba el número (id) que muestra el panel.' 212
$panelHint.ForeColor = [Drawing.Color]::DimGray; $panelHint.Font = New-Object Drawing.Font('Segoe UI', 8.5)

Add-Label 'TICKETS — impresora de Windows (no la de etiquetas)' 240 -Bold | Out-Null
$queueBox = New-Object Windows.Forms.ComboBox
$queueBox.Location = New-Object Drawing.Point(20, 263); $queueBox.Width = 520; $queueBox.DropDownStyle = 'DropDownList'
$form.Controls.Add($queueBox)
# Impresoras virtuales (PDF, OneNote, fax, XPS) y de red ajena no se ofrecen. Las de ETIQUETAS
# (stickers) hablan TSPL/ZPL, no ESC/POS: se ofrecen pero nunca se proponen, y elegirlas pide
# confirmación (un ticket ESC/POS en una de etiquetas sale como basura y gasta etiquetas).
$virtual = 'PDF|OneNote|Fax|XPS|Microsoft|Send To|Enviar a'
$LabelPattern = '(?i)label|etiqueta|sticker|zebra|\bzd\d|\bgk\d|\bgx\d|tsc|\bttp|\bte\d{3}|godex|\bhl\d|argox|\bos-2|\bcp-2|dymo|brother ql|\bql-|\bxp-3\d\d|\bxp-4\d\d|\bxp-dt|\bdt\d|\btdp'
$TicketPattern = '(?i)\bpos\b|pos-?\d|receipt|ticket|recibo|thermal|t[eé]rmica|\btm-|\btm\d|\bxp-?58|\bxp-?80|\bxp-?q|advance|adv-|bixolon|srp-|3nstar|\brpt\d|epson'
$all = @(Get-Printer | Where-Object { $_.Name -notmatch '\\\\' -and $_.Name -notmatch $virtual })
$queues = @($all | ForEach-Object { $_.Name })
$queues | ForEach-Object { [void]$queueBox.Items.Add($_) }
function Test-LabelPrinter($name) {
    $driver = ($all | Where-Object { $_.Name -eq $name } | Select-Object -First 1).DriverName
    return ("$name $driver" -match $LabelPattern)
}
$candidates = @($all | Where-Object { "$($_.Name) $($_.DriverName)" -match $TicketPattern -and -not (Test-LabelPrinter $_.Name) } | ForEach-Object { $_.Name })
if ($candidates.Count -eq 1) { $queueBox.SelectedItem = $candidates[0] }
# Con varias candidatas o ninguna, no se adivina: se elige a mano.

# --- LABEL-04: la impresora de ETIQUETAS (stickers), opcional ------------------------------
$NoLabels = '(sin impresora de etiquetas)'
Add-Label 'ETIQUETAS — impresora del panel (opcional)' 310 -Bold | Out-Null
$labelPanelBox = New-Object Windows.Forms.ComboBox
$labelPanelBox.Location = New-Object Drawing.Point(20, 333); $labelPanelBox.Width = 520; $labelPanelBox.DropDownStyle = 'DropDown'
$form.Controls.Add($labelPanelBox)
Add-Label 'ETIQUETAS — impresora de Windows' 370 -Bold | Out-Null
$labelQueueBox = New-Object Windows.Forms.ComboBox
$labelQueueBox.Location = New-Object Drawing.Point(20, 393); $labelQueueBox.Width = 520; $labelQueueBox.DropDownStyle = 'DropDownList'
$form.Controls.Add($labelQueueBox)
[void]$labelQueueBox.Items.Add($NoLabels)
$queues | ForEach-Object { [void]$labelQueueBox.Items.Add($_) }
$labelCandidates = @($all | Where-Object { Test-LabelPrinter $_.Name } | ForEach-Object { $_.Name })
$labelQueueBox.SelectedItem = if ($labelCandidates.Count -eq 1) { $labelCandidates[0] } else { $NoLabels }
# El rollo de cada impresora de etiquetas del panel (para la etiqueta de prueba).
$script:rolls = @{}

$testBox = New-Object Windows.Forms.CheckBox
$testBox.Text = 'Imprimir una prueba en cada impresora al terminar'; $testBox.Checked = $true
$testBox.Location = New-Object Drawing.Point(20, 430); $testBox.AutoSize = $true
$form.Controls.Add($testBox)

$installButton = New-Object Windows.Forms.Button
$installButton.Text = 'Instalar'; $installButton.Location = New-Object Drawing.Point(20, 465); $installButton.Size = New-Object Drawing.Size(520, 40)
$installButton.Font = New-Object Drawing.Font('Segoe UI', 11, [Drawing.FontStyle]::Bold)
$form.Controls.Add($installButton)

$logBox = New-Object Windows.Forms.TextBox
$logBox.Location = New-Object Drawing.Point(20, 520); $logBox.Size = New-Object Drawing.Size(520, 230)
$logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = 'Vertical'
$logBox.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($logBox)

function Say($text) {
    $logBox.AppendText("$text`r`n"); [Windows.Forms.Application]::DoEvents()
    try {
        New-Item -ItemType Directory -Force (Split-Path $LogFile) | Out-Null
        Add-Content -Path $LogFile -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $text) -Encoding UTF8
    } catch { }
}

function Get-Token {
    $token = ($tokenBox.Text -replace '\s', '')
    if ($token -notmatch '^bdpa_[A-Za-z0-9_\-]{20,}$') { throw 'El token no es válido: tiene que empezar por bdpa_ (cópielo del panel tal cual).' }
    return $token
}

function Get-Server {
    $server = $serverBox.Text.Trim().TrimEnd('/')
    if ($server -notmatch '^https://[A-Za-z0-9.\-]+(:\d+)?$') { throw 'El servidor tiene que ser una dirección https:// (por ejemplo https://mitienda.com).' }
    return $server
}

# --- Comprobar: ¿de quién es el token? ------------------------------------------------
$checkButton.Add_Click({
    try {
        $whoLabel.Text = 'Comprobando…'; $panelBox.Items.Clear(); $labelPanelBox.Items.Clear(); $script:rolls = @{}
        [Windows.Forms.Application]::DoEvents()
        $token = Get-Token; $server = Get-Server
        try {
            $me = Invoke-RestMethod -Uri "$server/api/v1/print-agent/me/" -Headers @{ Authorization = "PrintAgent $token" } -UserAgent $UserAgent -TimeoutSec 20
        } catch {
            $code = [int]$_.Exception.Response.StatusCode
            if ($code -eq 401) { throw 'El servidor no reconoce ese token (mal copiado o revocado).' }
            if ($code -eq 404) {
                $whoLabel.Text = 'Token correcto, pero este servidor todavía no dice qué impresora es: escriba su número (id).'
                $whoLabel.ForeColor = [Drawing.Color]::DarkOrange
                return
            }
            throw "No se pudo consultar el servidor ($($_.Exception.Message))."
        }
        $whoLabel.Text = "✔ $($me.company) · $($me.branch) · agente «$($me.agent)»"
        $whoLabel.ForeColor = [Drawing.Color]::DarkGreen
        $usb = @($me.printers | Where-Object { $_.connection -eq 'local_queue' })
        # LABEL-04: un servidor anterior no dice el tipo: todas son de tickets.
        $ticketPrinters = @($usb | Where-Object { $_.role -ne 'label' })
        $labelPrinters = @($usb | Where-Object { $_.role -eq 'label' })
        foreach ($p in $ticketPrinters) { [void]$panelBox.Items.Add("$($p.id) - $($p.name)$(if ($p.auto_print) { ' (automática)' } else { ' (sólo reimpresión)' })") }
        foreach ($p in $labelPrinters) {
            $script:rolls["$($p.id)"] = [string]$p.label_roll
            $roll = if ($p.label_roll) { " · rollo $($p.label_roll) mm" } else { ' · sin rollo marcado' }
            [void]$labelPanelBox.Items.Add("$($p.id) - $($p.name)$roll$(if ($p.auto_print) { ' (automática)' } else { '' })")
        }
        if ($ticketPrinters.Count -ge 1) { $panelBox.SelectedIndex = 0 }
        else { $panelHint.Text = 'Esa sucursal no tiene ninguna impresora de tickets «Equipo del agente (USB)»: créela en el panel.' }
        if ($labelPrinters.Count -eq 1) { $labelPanelBox.SelectedIndex = 0 }
    } catch {
        $whoLabel.Text = "✖ $($_.Exception.Message)"; $whoLabel.ForeColor = [Drawing.Color]::Firebrick
    }
})

# --- Pasos de la instalación ------------------------------------------------------------
function Install-Python {
    if (Test-Path (Join-Path $PythonDir 'pythonw.exe')) {
        $ok = 'python.exe', 'pythonw.exe' | ForEach-Object { (Get-AuthenticodeSignature (Join-Path $PythonDir $_)).Status -eq 'Valid' }
        if ($ok -notcontains $false) { Say "Python ya instalado en $PythonDir"; return }
    }
    Say "Descargando Python $PythonVersion oficial…"
    $zip = Join-Path $env:TEMP "python-$PythonVersion-embed-amd64.zip"
    Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $zip
    if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $PythonSha256) { Remove-Item $zip -Force; throw 'El Python descargado no coincide con el oficial (SHA-256): no se instala.' }
    New-Item -ItemType Directory -Force $PythonDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $PythonDir -Force
    Remove-Item $zip -Force
    foreach ($e in 'python.exe', 'pythonw.exe') {
        $sig = Get-AuthenticodeSignature (Join-Path $PythonDir $e)
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw "Firma inválida en $e" }
    }
    Say "Python instalado en $PythonDir (firma de la PSF verificada)"
}

function New-RandomPassword {
    $bytes = New-Object byte[] 36; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ConvertTo-SecureString ([Convert]::ToBase64String($bytes) + 'a1!A') -AsPlainText -Force
}

function Grant-BatchLogon($sid) {
    if (-not ('BdsLsa' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BdsLsa {
    [StructLayout(LayoutKind.Sequential)] struct UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public int Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr SystemName, ref OBJECT_ATTRIBUTES ObjectAttributes, int DesiredAccess, out IntPtr PolicyHandle);
    [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr PolicyHandle, byte[] AccountSid, UNICODE_STRING[] UserRights, int CountOfRights);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr h);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint s);
    public static void Add(byte[] sid, string right) {
        var oa = new OBJECT_ATTRIBUTES(); oa.Length = Marshal.SizeOf(oa); IntPtr h;
        uint s = LsaOpenPolicy(IntPtr.Zero, ref oa, 0x00000800 | 0x00000010, out h);
        if (s != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(s));
        try {
            var us = new UNICODE_STRING(); us.Buffer = Marshal.StringToHGlobalUni(right);
            us.Length = (ushort)(right.Length * 2); us.MaximumLength = (ushort)(us.Length + 2);
            s = LsaAddAccountRights(h, sid, new[] { us }, 1);
            Marshal.FreeHGlobal(us.Buffer);
            if (s != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(s));
        } finally { LsaClose(h); }
    }
}
'@
    }
    $bytes = New-Object byte[] $sid.BinaryLength; $sid.GetBinaryForm($bytes, 0)
    [BdsLsa]::Add($bytes, 'SeBatchLogonRight')
}

function Install-Account {
    $password = New-RandomPassword
    if (Get-LocalUser -Name $Account -ErrorAction SilentlyContinue) {
        Set-LocalUser -Name $Account -Password $password
    } else {
        New-LocalUser -Name $Account -Password $password -PasswordNeverExpires -UserMayNotChangePassword `
            -AccountNeverExpires -Description 'Agente de impresion' | Out-Null
    }
    if (-not (Get-LocalGroupMember -SID 'S-1-5-32-545' | Where-Object { $_.Name -eq $AgentUser })) {
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $AgentUser
    }
    if (Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object { $_.Name -eq $AgentUser }) { throw "$AgentUser es administrador: no se instala así." }
    Grant-BatchLogon (Get-LocalUser -Name $Account).SID
    Say "Cuenta estándar $AgentUser lista (no administradora; contraseña aleatoria que nadie conoce)"
    return $password
}

function Stop-Agent {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'PrintAgent\\agent\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

function Write-Config($server, $token, $routes) {
    New-Item -ItemType Directory -Force (Join-Path $Target 'data') | Out-Null
    $config = Join-Path $Target 'config.json'
    if (Test-Path -LiteralPath $config) {
        # Un config.json de una instalación anterior (quizá sin permisos legibles): se recupera y se reemplaza.
        & takeown.exe /F $config /A | Out-Null
        & icacls.exe $config /reset | Out-Null
        Remove-Item -LiteralPath $config -Force
    }
    $json = [ordered]@{
        server = $server; token = $token; poll_seconds = 3; printer_timeout = 10; spool_wait_seconds = 20
        journal = 'data\print-agent-journal.json'; log_file = 'data\print-agent.log'
        printers = $routes
    } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($config, $json, (New-Object Text.UTF8Encoding($false)))
    Say 'config.json escrito (el token no se muestra ni se registra)'
}

function Invoke-AsAgent($password, $arguments) {
    # Una tarea de un solo uso con la identidad del agente: así se comprueba lo que él puede hacer.
    $out = Join-Path $Target 'data\comprobacion.txt'
    if (Test-Path $out) { Remove-Item $out -Force }
    $py = Join-Path $PythonDir 'python.exe'
    $cmd = "/c `"`"$py`" -I `"$Target\agent.py`" --config `"$Target\config.json`" $arguments > `"$out`" 2>&1`""
    $name = "$TaskName - comprobacion"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
    try {
        Register-ScheduledTask -TaskName $name -Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmd -WorkingDirectory $Target) `
            -User $AgentUser -Password ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)) -RunLevel Limited -Force -ErrorAction Stop | Out-Null
    } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    Start-ScheduledTask -TaskName $name
    for ($i = 0; $i -lt 60 -and (Get-ScheduledTask -TaskName $name).State -eq 'Running'; $i++) { Start-Sleep -Seconds 1 }
    Start-Sleep -Seconds 1
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    $lines = if (Test-Path $out) { Get-Content $out; Remove-Item $out -Force } else { @('(sin salida)') }
    return $lines
}

function Send-TestLabel($queue, $roll) {
    # LABEL-04: una etiqueta TSPL con las fuentes de la impresora (ASCII), del tamaño del rollo del panel.
    if ($roll -notmatch '^(\d{2,3})x(\d{2,3})$') { Say "Etiqueta de prueba omitida: marque en el panel el rollo cargado (por ejemplo 30x20)."; return }
    $py = Join-Path $PythonDir 'python.exe'
    $code = @"
import sys, datetime; sys.path.insert(0, r'$Target')
import agent
w, h = sys.argv[2].split('x')
lines = ['SIZE %s mm,%s mm' % (w, h), 'GAP 2 mm,0 mm', 'DIRECTION 0', 'CLS',
         'TEXT 16,16,"2",0,1,1,"ETIQUETAS OK"', 'TEXT 16,56,"1",0,1,1,"%s"' % datetime.datetime.now().strftime('%d/%m/%Y %H:%M'),
         'PRINT 1,1', '']
agent.send_to_windows_queue(sys.argv[1], '\r\n'.join(lines).encode('ascii'), 10, 20, document='Prueba de etiquetas')
print('etiqueta de prueba entregada a la impresora de etiquetas')
"@
    $file = Join-Path $env:TEMP 'bds-etiqueta-prueba.py'
    [IO.File]::WriteAllText($file, $code, (New-Object Text.UTF8Encoding($false)))
    try { $out = & $py -I $file $queue $roll 2>&1 | Out-String } finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
    Say ($out.Trim())
}

function Send-TestTicket($queue, $who) {
    $py = Join-Path $PythonDir 'python.exe'
    $code = @"
import sys; sys.path.insert(0, r'$Target')
import agent, datetime
ESC, GS = b'\x1b', b'\x1d'
def t(s): return s.encode('cp858', 'replace') + b'\n'
ticket = ESC + b'@' + ESC + b't' + bytes([19]) + ESC + b'a\x01' + ESC + b'E\x01' + t('IMPRESORA INSTALADA') + ESC + b'E\x00'
ticket += t(sys.argv[1][:46]) + t(datetime.datetime.now().strftime('%d/%m/%Y %H:%M')) + t('Prueba: Ñ ñ á é í ó ú ü') + ESC + b'd\x03' + GS + b'V\x42\x00'
agent.send_to_windows_queue(sys.argv[2], ticket, 10, 20, document='Prueba de instalacion')
print('ticket de prueba entregado a la impresora')
"@
    $file = Join-Path $env:TEMP 'bds-ticket-prueba.py'
    [IO.File]::WriteAllText($file, $code, (New-Object Text.UTF8Encoding($false)))
    try { $out = & $py -I $file $who $queue 2>&1 | Out-String } finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
    Say ($out.Trim())
}

$installButton.Add_Click({
    $installButton.Enabled = $false; $checkButton.Enabled = $false
    try {
        $server = Get-Server; $token = Get-Token
        $printerId = ([string]$panelBox.Text -split '\s')[0]
        if ($printerId -notmatch '^[1-9][0-9]{0,9}$') { throw 'Elija la impresora del panel (o escriba su número).' }
        $queue = [string]$queueBox.SelectedItem
        if (-not $queue) { throw 'Elija la impresora de Windows de TICKETS.' }
        if (Test-LabelPrinter $queue) {
            $answer = [Windows.Forms.MessageBox]::Show(
                "«$queue» parece una impresora de ETIQUETAS (stickers). Los tickets salen en una térmica de tickets: en una de etiquetas saldría basura.`r`n`r`n¿Seguro que es la de tickets?",
                'Revise la impresora', 'YesNo', 'Warning')
            if ($answer -ne 'Yes') { throw 'Instalación cancelada: elija la impresora de tickets.' }
        }
        $routes = [ordered]@{ "id:$printerId" = [ordered]@{ transport = 'windows_raw'; queue = $queue } }
        # LABEL-04: la de etiquetas, si se eligió. Las dos o ninguna: un número sin cola (o al revés) no se adivina.
        $labelId = ([string]$labelPanelBox.Text -split '\s')[0]
        $labelQueue = [string]$labelQueueBox.SelectedItem
        if ($labelQueue -eq $NoLabels) { $labelQueue = '' }
        if ($labelId -and -not $labelQueue) { throw 'Eligió la impresora de etiquetas del panel: elija también su impresora de Windows (o borre el número).' }
        if ($labelQueue -and -not $labelId) { throw 'Eligió la impresora de Windows de etiquetas: elija también la del panel (pulse «Comprobar»).' }
        if ($labelId) {
            if ($labelId -notmatch '^[1-9][0-9]{0,9}$') { throw 'El número de la impresora de etiquetas no es válido.' }
            if ($labelId -eq $printerId) { throw 'La impresora de tickets y la de etiquetas del panel tienen que ser distintas.' }
            if ($labelQueue -eq $queue) { throw 'Los tickets y las etiquetas tienen que salir por impresoras de Windows distintas.' }
            if (-not (Test-LabelPrinter $labelQueue)) {
                $answer = [Windows.Forms.MessageBox]::Show(
                    "«$labelQueue» no parece una impresora de ETIQUETAS. Las etiquetas van en TSPL: en una de tickets saldría basura.`r`n`r`n¿Seguro que es la de etiquetas?",
                    'Revise la impresora', 'YesNo', 'Warning')
                if ($answer -ne 'Yes') { throw 'Instalación cancelada: elija la impresora de etiquetas.' }
            }
            $routes["id:$labelId"] = [ordered]@{ transport = 'windows_raw'; queue = $labelQueue }
        }
        Say "== Instalando: tickets id:$printerId en «$queue»$(if ($labelId) { "; etiquetas id:$labelId en «$labelQueue»" })"

        Install-Python
        $password = Install-Account
        foreach ($name in @($queue) + @($labelQueue | Where-Object { $_ })) {
            & rundll32.exe printui.dll,PrintUIEntry /Xs /n $name attributes +queued
            Say "Cola «$name»: imprimir después de la última página"
        }
        Stop-Agent
        Write-Config $server $token $routes
        Remove-Variable token

        Say 'Permisos, comprobación y tarea programada (install-task.ps1)…'
        $installer = Join-Path $here 'install-task.ps1'
        $result = & $installer -AgentUser $AgentUser -AgentPassword $password -Python (Join-Path $PythonDir 'pythonw.exe') 2>&1 | Out-String
        $result -split "`r?`n" | Where-Object { $_ -match '^(OK |MAL )|Tarea registrada' } | ForEach-Object { Say $_ }

        Say "Comprobación con la identidad de ${AgentUser}:"
        $check = Invoke-AsAgent $password '--check'
        $check | Where-Object { $_ } | ForEach-Object { Say "  $_" }
        if (-not ($check -match '^OK .*servidor')) { throw 'La comprobación con la cuenta del agente no salió bien (vea las líneas MAL).' }

        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 8
        $running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'PrintAgent\\agent\.py' })
        if ($running.Count -lt 1) { throw 'La tarea no arrancó el agente.' }
        Say "Agente en marcha ($($running.Count) proceso) con la cuenta $AgentUser"

        $who = if ($whoLabel.Text -like '✔*') { $whoLabel.Text.Substring(1).Trim() } else { 'Impresora térmica' }
        if ($testBox.Checked) {
            Send-TestTicket $queue $who
            if ($labelId) { Send-TestLabel $labelQueue $script:rolls[$labelId] }
        }
        Say '== LISTO. En el panel, el agente debe aparecer «En línea».'
        [Windows.Forms.MessageBox]::Show('Impresoras instaladas. Revise las pruebas y que el agente aparezca «En línea» en el panel.',
            'Instalación terminada', 'OK', 'Information') | Out-Null
    } catch {
        Say "ERROR: $($_.Exception.Message)"
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'No se pudo instalar', 'OK', 'Error') | Out-Null
    } finally {
        $installButton.Enabled = $true; $checkButton.Enabled = $true
    }
})

[void]$form.ShowDialog()
