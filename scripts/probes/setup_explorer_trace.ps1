# Enable / disable WCF SOAP message tracing on Vault Explorer Pro 2025.
#
# Usage:
#   powershell -File setup_explorer_trace.ps1 -Mode Enable
#   powershell -File setup_explorer_trace.ps1 -Mode Disable
#
# Must be run elevated (Program Files write).

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Enable','Disable')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'

$cfgPath = 'C:\Program Files\Autodesk\Vault Client 2025\Explorer\Connectivity.VaultPro.exe.config'
$bakPath = $cfgPath + '.pre-trace-bak'
$logPath = 'C:\Temp\vault-explorer-soap.svclog'

if (-not (Test-Path $cfgPath)) {
    throw "Vault Explorer Pro config not found at $cfgPath"
}

# Confirm we have write access (proxy for elevation check).
try {
    [IO.File]::OpenWrite($cfgPath).Close()
} catch {
    Write-Host "ERROR: cannot write to $cfgPath -- run this script in an elevated PowerShell window."
    Write-Host "Hint: Start-Process powershell -Verb RunAs"
    exit 2
}

if ($Mode -eq 'Enable') {
    if (-not (Test-Path $bakPath)) {
        Copy-Item -LiteralPath $cfgPath -Destination $bakPath -Force
        Write-Host "Backed up config -> $bakPath"
    } else {
        Write-Host "Backup already exists at $bakPath (keeping original)"
    }

    # Make sure the log dir exists and clear any prior log so we capture only this session.
    [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath))
    if (Test-Path $logPath) { Remove-Item -LiteralPath $logPath -Force }

    [xml]$xml = Get-Content -LiteralPath $cfgPath -Raw
    $root = $xml.DocumentElement   # <configuration>

    # 1) Remove any prior diagnostics blocks we may have added (idempotent re-enable).
    $existingDiag = $root.SelectSingleNode('system.diagnostics')
    if ($existingDiag) { [void]$root.RemoveChild($existingDiag) }
    $existingSvcModel = $root.SelectSingleNode('system.serviceModel')
    if ($existingSvcModel) {
        $existingSvcDiag = $existingSvcModel.SelectSingleNode('diagnostics')
        if ($existingSvcDiag) { [void]$existingSvcModel.RemoveChild($existingSvcDiag) }
    }

    # 2) Insert <system.diagnostics> with switchValue=All so MessageLogging actually emits.
    $diagNode = $null
    if (-not $diagNode) {
        $diagXml = @"
<system.diagnostics>
  <sources>
    <source name="System.ServiceModel.MessageLogging" switchValue="All">
      <listeners>
        <add name="messages"
             type="System.Diagnostics.XmlWriterTraceListener"
             initializeData="$logPath" />
      </listeners>
    </source>
  </sources>
  <trace autoflush="true" />
</system.diagnostics>
"@
        $frag = $xml.CreateDocumentFragment()
        $frag.InnerXml = $diagXml
        [void]$root.AppendChild($frag)
        Write-Host '+ Added <system.diagnostics>'
    } else {
        Write-Host '= <system.diagnostics> already present, leaving as-is'
    }

    # 2) Insert <diagnostics> inside the existing <system.serviceModel> if absent.
    $svcModel = $root.SelectSingleNode('system.serviceModel')
    if (-not $svcModel) {
        # Create one if missing entirely.
        $svcXml = @"
<system.serviceModel>
  <diagnostics>
    <messageLogging logEntireMessage="true"
                    logMalformedMessages="true"
                    logMessagesAtServiceLevel="false"
                    logMessagesAtTransportLevel="true"
                    maxMessagesToLog="2000"
                    maxSizeOfMessageToLog="2000000" />
  </diagnostics>
</system.serviceModel>
"@
        $frag = $xml.CreateDocumentFragment()
        $frag.InnerXml = $svcXml
        [void]$root.AppendChild($frag)
        Write-Host '+ Added <system.serviceModel> with <diagnostics>'
    } else {
        $existingDiag = $svcModel.SelectSingleNode('diagnostics')
        if (-not $existingDiag) {
            $diagInnerXml = @"
<diagnostics>
  <messageLogging logEntireMessage="true"
                  logMalformedMessages="true"
                  logMessagesAtServiceLevel="false"
                  logMessagesAtTransportLevel="true"
                  maxMessagesToLog="2000"
                  maxSizeOfMessageToLog="2000000" />
</diagnostics>
"@
            $frag = $xml.CreateDocumentFragment()
            $frag.InnerXml = $diagInnerXml
            # Append as last child of <system.serviceModel>.
            [void]$svcModel.AppendChild($frag)
            Write-Host '+ Added <diagnostics> inside existing <system.serviceModel>'
        } else {
            Write-Host '= <diagnostics> already inside <system.serviceModel>, leaving as-is'
        }
    }

    $xml.Save($cfgPath)
    Write-Host ""
    Write-Host "Tracing ENABLED."
    Write-Host "  Log file:  $logPath"
    Write-Host ""
    Write-Host "Now do this manually:"
    Write-Host "  1. Close any running Vault Explorer windows."
    Write-Host "  2. Launch Vault Explorer Pro 2025."
    Write-Host "  3. Sign in, navigate to SF-001792 (or any WIP item)."
    Write-Host "  4. Right-click -> Edit Properties (or the UDP edit dialog)."
    Write-Host "  5. Change the 'Comments' field to: EXPLORER-PROBE-2026-05-02"
    Write-Host "  6. Click OK to commit."
    Write-Host "  7. Close Vault Explorer fully (so the log flushes)."
    Write-Host ""
    Write-Host "Then run:  powershell -File setup_explorer_trace.ps1 -Mode Disable"

} elseif ($Mode -eq 'Disable') {
    if (Test-Path $bakPath) {
        Copy-Item -LiteralPath $bakPath -Destination $cfgPath -Force
        Remove-Item -LiteralPath $bakPath -Force
        Write-Host "Restored config from backup. Tracing DISABLED."
    } else {
        # Fallback: strip the diagnostics blocks we added.
        [xml]$xml = Get-Content -LiteralPath $cfgPath -Raw
        $root = $xml.DocumentElement
        $diag = $root.SelectSingleNode('system.diagnostics')
        if ($diag) { [void]$root.RemoveChild($diag); Write-Host '- Removed <system.diagnostics>' }
        $svc = $root.SelectSingleNode('system.serviceModel')
        if ($svc) {
            $svcDiag = $svc.SelectSingleNode('diagnostics')
            if ($svcDiag) { [void]$svc.RemoveChild($svcDiag); Write-Host '- Removed <diagnostics> from <system.serviceModel>' }
        }
        $xml.Save($cfgPath)
        Write-Host "No backup found; stripped trace blocks instead. Tracing DISABLED."
    }
    if (Test-Path $logPath) {
        $size = (Get-Item -LiteralPath $logPath).Length
        Write-Host ("Captured log preserved at {0}  ({1} bytes)" -f $logPath, $size)
    } else {
        Write-Host "No log file at $logPath -- did Explorer actually run with tracing on?"
    }
}
