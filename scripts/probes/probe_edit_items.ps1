# One-off probe: sign in via Vault SDK ConnectionManager, then call
# ItemService.EditItems on the supplied id and dump every layer of the
# exception so we can see what error 1321 actually means.
#
# Usage:
#   powershell -STA -File probe_edit_items.ps1 -ItemId 114824
#   powershell -STA -File probe_edit_items.ps1 -ItemId 114823 -TryMaster

param(
    [Parameter(Mandatory = $true)] [int64]$ItemId,
    [switch]$TryMaster
)

$ErrorActionPreference = 'Stop'
$OutputEncoding         = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$SdkBin = if ($env:VAULT_SDK_BIN) { $env:VAULT_SDK_BIN } else { 'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64' }

# Ensure licensing native DLL is on PATH (mirrors vault_sdk.ps1)
$licDllNames   = @('AdskLicensingSDK_8.dll','AdskLicensingSDK_2.dll')
$licSearchDirs = @($SdkBin,
                   'C:\Program Files\Autodesk\Vault Client 2025\Explorer',
                   'C:\Program Files\Autodesk\Inventor 2025\Bin')
foreach ($d in $licSearchDirs) {
    foreach ($n in $licDllNames) {
        if (Test-Path (Join-Path $d $n)) {
            if ($env:PATH -notlike "*$d*") { $env:PATH = "$d;$env:PATH" }
            break
        }
    }
}

# Resolve handler with cache (avoid recursive AssemblyResolve)
$script:resolveDirs  = New-Object System.Collections.ArrayList
$script:resolveCache = @{}
$script:resolveHandler = {
    param($s, $e)
    $name = ($e.Name -split ',')[0].Trim()
    if ($script:resolveCache.ContainsKey($name)) { return $script:resolveCache[$name] }
    $script:resolveCache[$name] = $null
    foreach ($dir in $script:resolveDirs) {
        $cand = Join-Path $dir ($name + '.dll')
        if (Test-Path $cand) {
            try { $asm = [System.Reflection.Assembly]::LoadFrom($cand); $script:resolveCache[$name] = $asm; return $asm } catch {}
        }
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
if (-not ($r -and $r.Success)) {
    Write-Host "LOGIN FAILED:"
    if ($r -and $r.ErrorMessages) { foreach ($kv in $r.ErrorMessages.GetEnumerator()) { Write-Host "  $($kv.Key): $($kv.Value)" } }
    exit 1
}
$mgr = $r.Connection.WebServiceManager
Write-Host "Connected as $($cfg.vault.username) to $serverHost / $($cfg.vault.database)"
Write-Host "Connection.UserId = $($r.Connection.UserID)"

# Look up the actual item to confirm what id we should pass
Write-Host "`n--- GetItemsByIds($ItemId) (treat as rev id) ---"
try {
    $items = $mgr.ItemService.GetItemsByIds([int64[]]@($ItemId))
    foreach ($it in $items) {
        Write-Host "  rev.Id=$($it.Id) MasterId=$($it.MasterId) State=$($it.LfCycStateId) Locked=$($it.Locked) IsLatest=$($it.LatestItemRev)"
    }
} catch {
    Write-Host "GetItemsByIds threw: $($_.Exception.Message)"
}

if ($TryMaster) {
    Write-Host "`n--- GetLatestItemByItemMasterId($ItemId) (treat as master id) ---"
    try {
        $rev = $mgr.ItemService.GetLatestItemByItemMasterId([int64]$ItemId)
        if ($rev) { Write-Host "  rev.Id=$($rev.Id) MasterId=$($rev.MasterId) State=$($rev.LfCycStateId) Locked=$($rev.Locked)" }
    } catch {
        Write-Host "GetLatestItemByItemMasterId threw: $($_.Exception.Message)"
    }
}

Write-Host "`n--- EditItems([$ItemId]) ---"
try {
    $editable = $mgr.ItemService.EditItems([int64[]]@($ItemId))
    Write-Host "EditItems returned $($editable.Count) editable entries"
    foreach ($e in $editable) { Write-Host "  Id=$($e.Id) MasterId=$($e.MasterId)" }
    $mgr.ItemService.UndoEditItems([int64[]]@($ItemId)) | Out-Null
    Write-Host 'UndoEditItems ok'
} catch {
    $ex = $_.Exception
    Write-Host '=== TOP ==='
    Write-Host ("Type:    " + $ex.GetType().FullName)
    Write-Host ("Message: " + $ex.Message)
    $depth = 0
    $cur = $ex.InnerException
    while ($cur -and $depth -lt 6) {
        Write-Host ("--- inner[$depth] ---")
        Write-Host ("Type:    " + $cur.GetType().FullName)
        Write-Host ("Message: " + $cur.Message)
        foreach ($prop in 'ErrorCode','ErrCode','Reason','Description','Code','HResult','RestrictionTypeCode') {
            try {
                if ($cur.PSObject.Properties[$prop]) { Write-Host ("  $prop = " + $cur.$prop) }
            } catch {}
        }
        try {
            if ($cur.PSObject.Properties['Detail'] -and $cur.Detail) {
                Write-Host '  Detail XML:'
                Write-Host ('    ' + $cur.Detail.OuterXml)
            }
        } catch { Write-Host ("  Detail dump failed: " + $_.Exception.Message) }
        if ($cur -is [System.ServiceModel.FaultException]) {
            try {
                $fault = $cur.CreateMessageFault()
                Write-Host ("  FaultCode: " + $fault.Code.Name)
                if ($fault.HasDetail) {
                    $reader = $fault.GetReaderAtDetailContents()
                    Write-Host ("  Detail XML:")
                    Write-Host ($reader.ReadOuterXml())
                }
            } catch { Write-Host ("  (no fault detail: " + $_.Exception.Message + ")") }
        }
        $cur = $cur.InnerException
        $depth++
    }
}
