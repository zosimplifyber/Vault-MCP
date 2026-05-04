# Dump the SOAP fault Detail XML for the 1321 exception.
$ErrorActionPreference = 'Stop'
$SdkBin = 'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64'
if ($env:PATH -notlike "*$SdkBin*") { $env:PATH = "$SdkBin;$env:PATH" }
foreach ($n in 'Autodesk.Connectivity.WebServices.dll',
               'Autodesk.DataManagement.Client.Framework.dll',
               'Autodesk.DataManagement.Client.Framework.Vault.dll') {
    Add-Type -Path (Join-Path $SdkBin $n)
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content (Join-Path $projectRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$serverHost = $cfg.vault.servername -replace '^https?://','' -replace '/$',''
$afType = [Autodesk.DataManagement.Client.Framework.Vault.Currency.Connections.AuthenticationFlags]
$cm = [Autodesk.DataManagement.Client.Framework.Vault.Library]::ConnectionManager
$r = $cm.LogIn($serverHost, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password, $afType::Standard, $null)
if (-not $r.Success) { Write-Host 'login failed'; exit 1 }
$mgr = $r.Connection.WebServiceManager

try {
    $mgr.ItemService.EditItems([int64[]]@(67652)) | Out-Null
} catch {
    $vex = $_.Exception.InnerException
    if ($vex -and $vex.Detail) {
        Write-Host '=== SOAP fault Detail XML ==='
        Write-Host $vex.Detail.OuterXml
        Write-Host ''
        Write-Host '=== Pretty-printed ==='
        $sb = New-Object System.Text.StringBuilder
        $sw = New-Object System.IO.StringWriter $sb
        $xw = New-Object System.Xml.XmlTextWriter $sw
        $xw.Formatting = 'Indented'
        $vex.Detail.WriteTo($xw)
        $xw.Flush()
        Write-Host $sb.ToString()
    }
    if ($vex -and $vex.MessageId) {
        Write-Host ''
        Write-Host "=== Resolving MessageId $($vex.MessageId) ==="
        try {
            $resolved = $mgr.AdminService.GetServerErrorMessage($vex.MessageId, [string[]]@())
            Write-Host "  $resolved"
        } catch {
            Write-Host "  GetServerErrorMessage threw: $($_.Exception.Message)"
            # Try alternate signature
            try {
                $resolved = $mgr.AdminService.GetServerErrorMessage($vex.MessageId)
                Write-Host "  (alt) $resolved"
            } catch { }
        }
    }
}
