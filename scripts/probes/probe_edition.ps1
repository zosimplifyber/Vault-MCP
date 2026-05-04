$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$configPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
$cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
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

# Use the legacy WebServiceManager path with LicensingAgent.Client — checks
# out a per-machine Vault Client license seat. Requires a Vault Client
# install + activated license on the machine running this script.
$identities = New-Object Autodesk.Connectivity.WebServices.ServerIdentities
$identities.DataServer = $serverHost
$identities.FileServer = $serverHost
$cred = New-Object Autodesk.Connectivity.WebServicesTools.UserPasswordCredentials(
    $identities, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password,
    [Autodesk.Connectivity.WebServices.LicensingAgent]::Client
)
$mgr = New-Object Autodesk.Connectivity.WebServicesTools.WebServiceManager($cred)

Write-Host "=== Vault server product info ===" -ForegroundColor Cyan
try {
    $name = $mgr.InformationService.GetServerName()
    Write-Host "  Server name: $name"
} catch {
    Write-Host "  GetServerName threw: $($_.Exception.Message)" -ForegroundColor Red
}

try {
    $sysProds = $mgr.InformationService.GetSystemProducts()
    Write-Host "  System products on server:"
    foreach ($p in $sysProds) {
        $editionParts = @()
        if ($p.IsBasicEdition)        { $editionParts += 'Basic' }
        if ($p.IsWorkgroupEdition)    { $editionParts += 'Workgroup' }
        if ($p.IsCollaborationEdition){ $editionParts += 'Collaboration' }
        if ($p.IsProfessionalEdition) { $editionParts += 'Professional' }
        $edition = if ($editionParts.Count) { ($editionParts -join '+') } else { 'Unknown' }
        Write-Host ("    {0} v{1}  edition={2}" -f $p.Name, $p.Ver, $edition)
    }
} catch {
    Write-Host "  GetSystemProducts threw: $($_.Exception.Message)" -ForegroundColor Red
}

try {
    $supProds = $mgr.InformationService.GetSupportedProducts()
    Write-Host "  Supported products (this SDK build):"
    foreach ($p in $supProds) {
        $editionParts = @()
        if ($p.IsBasicEdition)        { $editionParts += 'Basic' }
        if ($p.IsWorkgroupEdition)    { $editionParts += 'Workgroup' }
        if ($p.IsCollaborationEdition){ $editionParts += 'Collaboration' }
        if ($p.IsProfessionalEdition) { $editionParts += 'Professional' }
        $edition = if ($editionParts.Count) { ($editionParts -join '+') } else { 'Unknown' }
        Write-Host ("    {0} v{1}  edition={2}" -f $p.Name, $p.Ver, $edition)
    }
} catch {
    Write-Host "  GetSupportedProducts threw: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "=== AuthService.SignInGetLicense (probes available license seats) ===" -ForegroundColor Cyan
try {
    # SignInGetLicense (svrName, dbName, userName, userPassword) returns the
    # license/seat info if one is available.
    $lic = $mgr.AuthService.SignInGetLicense($serverHost, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password)
    Write-Host "  License obtained:"
    $lic | Format-List | Out-String | Write-Host
} catch {
    Write-Host "  SignInGetLicense threw: $($_.Exception.Message)" -ForegroundColor Red
}
