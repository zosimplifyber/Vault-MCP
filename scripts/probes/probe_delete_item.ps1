# One-off cleanup: delete the SF-001793 test item we created during the
# Locks/EntityID investigation. Checks restrictions first, then deletes.
#
# Usage:
#   powershell -STA -File probe_delete_item.ps1 -MasterId 114825

param(
    [Parameter(Mandatory = $true)] [int64]$MasterId
)

$ErrorActionPreference = 'Stop'
$OutputEncoding         = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$SdkBin = if ($env:VAULT_SDK_BIN) { $env:VAULT_SDK_BIN } else { 'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64' }
$licDllNames   = @('AdskLicensingSDK_8.dll','AdskLicensingSDK_2.dll')
$licSearchDirs = @($SdkBin, 'C:\Program Files\Autodesk\Vault Client 2025\Explorer', 'C:\Program Files\Autodesk\Inventor 2025\Bin')
foreach ($d in $licSearchDirs) { foreach ($n in $licDllNames) { if (Test-Path (Join-Path $d $n)) { if ($env:PATH -notlike "*$d*") { $env:PATH = "$d;$env:PATH" }; break } } }

$script:resolveDirs  = New-Object System.Collections.ArrayList
$script:resolveCache = @{}
$script:resolveHandler = {
    param($s, $e)
    $name = ($e.Name -split ',')[0].Trim()
    if ($script:resolveCache.ContainsKey($name)) { return $script:resolveCache[$name] }
    $script:resolveCache[$name] = $null
    foreach ($dir in $script:resolveDirs) {
        $cand = Join-Path $dir ($name + '.dll')
        if (Test-Path $cand) { try { $a = [System.Reflection.Assembly]::LoadFrom($cand); $script:resolveCache[$name] = $a; return $a } catch {} }
    }
    return $null
}
[void]$script:resolveDirs.Add($SdkBin)
[System.AppDomain]::CurrentDomain.add_AssemblyResolve($script:resolveHandler)

foreach ($n in @('Autodesk.Connectivity.WebServices.dll',
                 'Autodesk.DataManagement.Client.Framework.dll',
                 'Autodesk.DataManagement.Client.Framework.Vault.dll')) {
    Add-Type -Path (Join-Path $SdkBin $n)
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content (Join-Path $projectRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$serverHost = $cfg.vault.servername -replace '^https?://', '' -replace '/$', ''

$afType = [Autodesk.DataManagement.Client.Framework.Vault.Currency.Connections.AuthenticationFlags]
$cm     = [Autodesk.DataManagement.Client.Framework.Vault.Library]::ConnectionManager
$r = $cm.LogIn($serverHost, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password, $afType::Standard, $null)
if (-not ($r -and $r.Success)) { Write-Host 'LOGIN FAILED'; exit 1 }
$mgr = $r.Connection.WebServiceManager
Write-Host "Connected as $($cfg.vault.username) (UserId=$($r.Connection.UserID))"

# Confirm we're talking about the right item.
$rev = $mgr.ItemService.GetLatestItemByItemMasterId([int64]$MasterId)
if (-not $rev) { Write-Host "No item found for masterId $MasterId"; exit 1 }
Write-Host ("`nTarget: master={0}  RevId={1}  Number={2}  State={3}" -f $rev.MasterId, $rev.RevId, $rev.ItemNum, $rev.LfCycStateId)

# Check restrictions before deleting.
Write-Host "`n--- GetItemDeleteRestrictionsByIds ---"
try {
    $restrictions = $mgr.ItemService.GetItemDeleteRestrictionsByIds([int64[]]@($MasterId))
    if (-not $restrictions -or $restrictions.Count -eq 0) {
        Write-Host '  No restrictions reported. Safe to delete.'
    } else {
        foreach ($rr in $restrictions) {
            Write-Host ("  Restriction: Code={0} EntityId={1} Reason={2}" -f $rr.RestrictionTypeCode, $rr.EntityId, $rr.RestrictionReason)
        }
    }
} catch {
    Write-Host ("  Could not check restrictions: " + $_.Exception.Message)
}

Write-Host "`n--- DeleteItems([$MasterId]) ---"
try {
    $mgr.ItemService.DeleteItems([int64[]]@([int64]$MasterId)) | Out-Null
    Write-Host "DeleteItems OK -- master $MasterId deleted"
} catch {
    Write-Host '!!! DeleteItems failed !!!'
    $ex = $_.Exception
    Write-Host ("Type:    " + $ex.GetType().FullName)
    Write-Host ("Message: " + $ex.Message)
    $cur = $ex.InnerException
    $depth = 0
    while ($cur -and $depth -lt 4) {
        Write-Host ("--- inner[$depth] ---")
        Write-Host ("Type:    " + $cur.GetType().FullName)
        Write-Host ("Message: " + $cur.Message)
        foreach ($prop in 'ErrorCode','Code') {
            try { if ($cur.PSObject.Properties[$prop]) { Write-Host ("  $prop = " + $cur.$prop) } } catch {}
        }
        try {
            if ($cur.PSObject.Properties['Detail'] -and $cur.Detail) {
                Write-Host ('  Detail XML: ' + $cur.Detail.OuterXml)
            }
        } catch {}
        $cur = $cur.InnerException
        $depth++
    }
    exit 1
}
