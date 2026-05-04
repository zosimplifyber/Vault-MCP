# Sweep all AuthenticationFlags combos calling EditItems on the same item
# to see if any session principal type avoids the Locks.EntityID NULL bug.
#
# Hypothesis: Vault Explorer uses an Autodesk Account / SSO session whose
# SystemPrincipal can ResolveDatabaseName during transaction setup, while
# our ConnectionManager.LogIn(Standard) session can't, leading to the
# BEGIN/COMMIT mismatch and NULL EntityID.
#
# Usage:
#   powershell -STA -File probe_edit_items_authflags.ps1 -ItemId 114824

param(
    [Parameter(Mandatory = $true)] [int64]$ItemId
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

$combos = @(
    @{ name = 'Standard';                              flags = $afType::Standard },
    @{ name = 'Standard|ServerLicense';                flags = ($afType::Standard -bor $afType::ServerLicense) },
    @{ name = 'Standard|ReadOnly';                     flags = ($afType::Standard -bor $afType::ReadOnly) },
    @{ name = 'WindowsAuthentication';                 flags = $afType::WindowsAuthentication },
    @{ name = 'WindowsAuthenticationWithCredentials';  flags = $afType::WindowsAuthenticationWithCredentials },
    @{ name = 'AutodeskAccount';                       flags = $afType::AutodeskAccount }
)

foreach ($c in $combos) {
    Write-Host ("`n========== Trying combo: {0} ==========" -f $c.name)
    try {
        $r = $cm.LogIn($serverHost, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password, $c.flags, $null)
        if (-not ($r -and $r.Success)) {
            Write-Host ("  Sign-in failed: {0}" -f ($(if ($r.ErrorMessages) { ($r.ErrorMessages.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }) -join '; ' } else { '(no detail)' })))
            continue
        }
        $mgr = $r.Connection.WebServiceManager
        Write-Host ("  Sign-in OK. UserId={0} Flags={1}" -f $r.Connection.UserID, $r.Connection.AuthenticationFlags)
        try {
            $editable = $mgr.ItemService.EditItems([int64[]]@($ItemId))
            Write-Host ("  EditItems OK -- {0} editable" -f $editable.Count)
            $mgr.ItemService.UndoEditItems([int64[]]@($ItemId)) | Out-Null
            Write-Host '  UndoEditItems OK'
        } catch {
            $ex = $_.Exception
            $msg = $ex.Message
            $inner = $ex.InnerException
            $code = if ($inner -and $inner.PSObject.Properties['ErrorCode']) { $inner.ErrorCode } else { '?' }
            Write-Host ("  EditItems FAILED: code={0}  msg={1}" -f $code, $msg)
        }
        try { $cm.LogOut($r.Connection) | Out-Null } catch {}
    } catch {
        Write-Host ("  Sign-in threw: " + $_.Exception.Message)
    }
}
