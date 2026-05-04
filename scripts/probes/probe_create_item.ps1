# One-off probe: create a brand-new item via ItemService.AddItemRevision,
# attempt to commit it, and (if successful) try EditItems on it.
#
# Goal: confirm whether the Simplifyber.dbo.Locks "EntityID NULL" failure
# affects item creation as well as item editing — i.e. is the Locks bug
# truly vault-wide, or is it only triggered by existing item revisions.
#
# Usage:
#   powershell -STA -File probe_create_item.ps1

param()

$ErrorActionPreference = 'Stop'
$OutputEncoding         = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$SdkBin = if ($env:VAULT_SDK_BIN) { $env:VAULT_SDK_BIN } else { 'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64' }

# Licensing DLL on PATH
$licDllNames   = @('AdskLicensingSDK_8.dll','AdskLicensingSDK_2.dll')
$licSearchDirs = @($SdkBin, 'C:\Program Files\Autodesk\Vault Client 2025\Explorer', 'C:\Program Files\Autodesk\Inventor 2025\Bin')
foreach ($d in $licSearchDirs) { foreach ($n in $licDllNames) { if (Test-Path (Join-Path $d $n)) { if ($env:PATH -notlike "*$d*") { $env:PATH = "$d;$env:PATH" }; break } } }

# AssemblyResolve cache
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
    Write-Host "LOGIN FAILED."
    if ($r -and $r.ErrorMessages) { foreach ($kv in $r.ErrorMessages.GetEnumerator()) { Write-Host "  $($kv.Key): $($kv.Value)" } }
    exit 1
}
$mgr = $r.Connection.WebServiceManager
Write-Host "Connected as $($cfg.vault.username) (UserId=$($r.Connection.UserID)) to $serverHost / $($cfg.vault.database)"

function Dump-Exception($ex) {
    Write-Host '=== EXCEPTION ==='
    Write-Host ("Type:    " + $ex.GetType().FullName)
    Write-Host ("Message: " + $ex.Message)
    $depth = 0
    $cur = $ex.InnerException
    while ($cur -and $depth -lt 6) {
        Write-Host ("--- inner[$depth] ---")
        Write-Host ("Type:    " + $cur.GetType().FullName)
        Write-Host ("Message: " + $cur.Message)
        foreach ($prop in 'ErrorCode','Code','HResult') {
            try { if ($cur.PSObject.Properties[$prop]) { Write-Host ("  $prop = " + $cur.$prop) } } catch {}
        }
        try {
            if ($cur.PSObject.Properties['Detail'] -and $cur.Detail) {
                Write-Host '  Detail XML:'
                Write-Host ('    ' + $cur.Detail.OuterXml)
            }
        } catch {}
        $cur = $cur.InnerException
        $depth++
    }
}

# Find an Item category we can create into
Write-Host "`n--- Categories ---"
$cats = $mgr.CategoryService.GetCategoriesByEntityClassId('ITEM', $true)
foreach ($c in $cats) { Write-Host ("  CatId={0,4}  Name={1}" -f $c.Id, $c.Name) }
$catId = ($cats | Where-Object { $_.Name -eq 'Part - Engineering' } | Select-Object -First 1).Id
if (-not $catId) { $catId = $cats[0].Id }
Write-Host "Using categoryId=$catId"

Write-Host "`n--- AddItemRevision(categoryId=$catId) ---"
try {
    $newItem = $mgr.ItemService.AddItemRevision([int64]$catId)
    Write-Host "AddItemRevision succeeded:"
    Write-Host ("  rev.Id=$($newItem.Id)  MasterId=$($newItem.MasterId)  Number=$($newItem.ItemNum)  State=$($newItem.LfCycStateId)")

    Write-Host "`n--- UpdateAndCommitItems([newItem]) ---"
    try {
        $arr = [Autodesk.Connectivity.WebServices.Item[]]@($newItem)
        $committed = $mgr.ItemService.UpdateAndCommitItems($arr)
        Write-Host "UpdateAndCommitItems returned $($committed.Count) item(s)"
        foreach ($i in $committed) {
            Write-Host ("  rev.Id=$($i.Id)  MasterId=$($i.MasterId)  Number=$($i.ItemNum)  State=$($i.LfCycStateId)")
        }

        $newRevId = [int64]$committed[0].Id
        Write-Host "`n--- EditItems([$newRevId]) on freshly created item ---"
        try {
            $editable = $mgr.ItemService.EditItems([int64[]]@($newRevId))
            Write-Host "EditItems returned $($editable.Count) editable entries -- LOCKS WORK on fresh items!"
            $mgr.ItemService.UndoEditItems([int64[]]@($newRevId)) | Out-Null
            Write-Host 'UndoEditItems ok'
        } catch {
            Write-Host '!!! EditItems failed on a freshly created item -- Locks bug is vault-wide !!!'
            Dump-Exception $_.Exception
        }
    } catch {
        Write-Host '!!! UpdateAndCommitItems failed !!!'
        Dump-Exception $_.Exception
        Write-Host "`n--- attempting UndoEditItems([$($newItem.Id)]) to release the uncommitted item ---"
        try {
            $mgr.ItemService.UndoEditItems([int64[]]@($newItem.Id)) | Out-Null
            Write-Host 'UndoEditItems ok'
        } catch { Write-Host ("UndoEditItems also failed: " + $_.Exception.Message) }
    }
} catch {
    Write-Host '!!! AddItemRevision failed -- create-path also blocked !!!'
    Dump-Exception $_.Exception
}
