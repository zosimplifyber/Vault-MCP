param(
    [string]$ConfigPath = ''
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $ConfigPath) {
    $ConfigPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
}
if (-not (Test-Path $ConfigPath)) { throw "config.json not found at $ConfigPath" }

$cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$serverHost = ($cfg.vault.servername -replace '^https?://', '' -replace '/$', '')

$SdkBin = if ($env:VAULT_SDK_BIN) { $env:VAULT_SDK_BIN } else { 'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64' }
# Per AU SD321955 page 14: PowerShell needs the Autodesk Licensing DLL on
# its load path. Without this, every writable LicensingAgent fails with
# VaultLicenseException even when a seat is actually available.
foreach ($d in @($SdkBin, 'C:\Program Files\Autodesk\Vault Client 2025\Explorer')) {
    foreach ($n in @('AdskLicensingSDK_8.dll','AdskLicensingSDK_2.dll')) {
        if (Test-Path (Join-Path $d $n)) {
            if ($env:PATH -notlike "*$d*") { $env:PATH = "$d;$env:PATH" }
        }
    }
}
foreach ($n in 'Autodesk.Connectivity.WebServices.dll',
                 'Autodesk.DataManagement.Client.Framework.dll',
                 'Autodesk.DataManagement.Client.Framework.Vault.dll') {
    Add-Type -Path (Join-Path $SdkBin $n)
}

$identities = New-Object Autodesk.Connectivity.WebServices.ServerIdentities
$identities.DataServer = $serverHost
$identities.FileServer = $serverHost
$cred = New-Object Autodesk.Connectivity.WebServicesTools.UserPasswordCredentials(
    $identities, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password,
    [Autodesk.Connectivity.WebServices.LicensingAgent]::Client
)
$mgr = New-Object Autodesk.Connectivity.WebServicesTools.WebServiceManager($cred)

Write-Host "=== Logged in as: $($cfg.vault.username) ===" -ForegroundColor Cyan

# Permission codes we care about for item writes
$wanted = @(
    'ITEM_EDT',
    'ITEM_REL',
    'ITEM_VIEW',
    'ITEM_REVISE',
    'FILE_EDT',
    'FILE_VIEW',
    'CATEGORY_EDT',
    'CATEGORY_VIEW',
    'BOM_EDT'
)

Write-Host "`n=== AdminService.CheckRolePermissions on this user's session ===" -ForegroundColor Cyan
try {
    $result = $mgr.AdminService.CheckRolePermissions($wanted)
    for ($i=0; $i -lt $wanted.Count; $i++) {
        $code = $wanted[$i]
        $has  = $result[$i]
        Write-Host ("  {0,-25} {1}" -f $code, $has)
    }
} catch {
    Write-Host "  CheckRolePermissions threw: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n=== AdminService.GetAllPermissions (sample, item-related only) ===" -ForegroundColor Cyan
try {
    $allPerms = $mgr.AdminService.GetAllPermissions()
    $allPerms | Where-Object { $_.PermSysName -match '(?i)item|categor|propert' } | ForEach-Object {
        Write-Host ("  {0}  ({1})  {2}" -f $_.PermSysName, $_.Id, $_.Descr)
    }
} catch {
    Write-Host "  GetAllPermissions threw: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n=== Roles assigned to current user (requires admin) ===" -ForegroundColor Cyan
try {
    $allUsers = $mgr.AdminService.GetAllUsers()
    $me = $allUsers | Where-Object { $_.Name -eq $cfg.vault.username } | Select-Object -First 1
    if ($me) {
        Write-Host "  User Id: $($me.Id), Active: $($me.IsActive), IsSysAdmin: $($me.IsSysAdmin)"
        $myRoles = $mgr.AdminService.GetRolesByUserId($me.Id)
        foreach ($r in $myRoles) {
            Write-Host ("    role: {0}  ({1})" -f $r.Name, $r.Descr)
        }
        $myPerms = $mgr.AdminService.GetPermissionsByUserId($me.Id)
        Write-Host "`n  Permissions (item-related):"
        $myPerms | Where-Object { $_.SysName -match '(?i)item|categor|propert|file' } | ForEach-Object {
            Write-Host ("    {0}" -f $_.SysName)
        }
    } else {
        Write-Host "  Current user not found in GetAllUsers result." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  Role probe threw: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  (This is expected if the configured account lacks admin permissions.)" -ForegroundColor DarkGray
}
