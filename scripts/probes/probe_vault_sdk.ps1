# Probe the Vault .NET SDK from PowerShell — no Python, no pythonnet needed.
#
# PowerShell 5.1 (the default on Windows) hosts .NET Framework natively, so
# we can load the Vault SDK assemblies via Add-Type and call them the same
# way the VB sample does. If this script prints "ALL CHECKS PASSED" we know
# the SDK route works on this machine and we can confidently build the
# Python wrapper.
#
# Run:
#     powershell -ExecutionPolicy Bypass -File scripts\probe_vault_sdk.ps1

$ErrorActionPreference = 'Stop'

# Default Vault 2025 SDK install path. Override via env var if yours differs.
$SdkBin = if ($env:VAULT_SDK_BIN) {
    $env:VAULT_SDK_BIN
} else {
    'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64'
}

# ---------------------------------------------------------------------------
# Console helpers — use single-quoted literal strings everywhere we don't
# need variable interpolation, so PowerShell never tries to parse parens
# inside our text as sub-expressions.
# ---------------------------------------------------------------------------

function Write-Step([int]$n, [string]$msg) {
    Write-Host ''
    Write-Host ('  STEP {0}: {1}' -f $n, $msg) -ForegroundColor Cyan
}
function Write-Ok([string]$msg)   { Write-Host ('    [OK]   ' + $msg) -ForegroundColor Green }
function Write-Fail([string]$msg) { Write-Host ('    [FAIL] ' + $msg) -ForegroundColor Red }
function Write-Note([string]$msg) { Write-Host ('           ' + $msg) -ForegroundColor DarkGray }

Write-Host ''
Write-Host 'Vault SDK + PowerShell probe' -ForegroundColor Cyan
Write-Host '============================' -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Load config.json
# ---------------------------------------------------------------------------

Write-Step 1 'Read config.json'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath  = Join-Path $projectRoot 'config.json'
if (-not (Test-Path $configPath)) {
    Write-Fail ('config.json not found at ' + $configPath)
    exit 1
}
$cfg     = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$server  = $cfg.vault.servername
$db      = $cfg.vault.database
$user    = $cfg.vault.username
$pass    = $cfg.vault.password
if (-not $server -or -not $db -or -not $user -or -not $pass) {
    Write-Fail 'config.json is missing one of vault.servername/database/username/password'
    exit 1
}
Write-Ok ('server={0}  database={1}  user={2}' -f $server, $db, $user)

# UserPasswordCredentials wants the bare hostname (no scheme).
$serverHost = $server -replace '^https?://', '' -replace '/$', ''
Write-Note ('using host=' + $serverHost + ' for UserPasswordCredentials')

# ---------------------------------------------------------------------------
# 2. Locate the SDK
# ---------------------------------------------------------------------------

Write-Step 2 'Locate the Vault SDK assemblies'
if (-not (Test-Path $SdkBin)) {
    Write-Fail ('SDK folder not found: ' + $SdkBin)
    Write-Note 'Set the VAULT_SDK_BIN environment variable to override.'
    exit 1
}
$needed = @(
    # WebServices.dll bundles both Autodesk.Connectivity.WebServices AND
    # Autodesk.Connectivity.WebServicesTools namespaces in Vault 2025 — there
    # is no separate WebServicesTools.dll any more.
    'Autodesk.Connectivity.WebServices.dll',
    'Autodesk.DataManagement.Client.Framework.dll',
    'Autodesk.DataManagement.Client.Framework.Vault.dll'
)
$missing = $needed | Where-Object { -not (Test-Path (Join-Path $SdkBin $_)) }
if ($missing.Count -gt 0) {
    Write-Fail ('Missing assemblies in ' + $SdkBin + ': ' + ($missing -join ', '))
    exit 1
}
Write-Ok ('All ' + $needed.Count + ' required assemblies present at ' + $SdkBin)

# ---------------------------------------------------------------------------
# 3. Load the SDK assemblies into the AppDomain
# ---------------------------------------------------------------------------

Write-Step 3 'Add-Type each SDK assembly'
try {
    foreach ($n in $needed) {
        Add-Type -Path (Join-Path $SdkBin $n)
    }
} catch {
    Write-Fail ('Add-Type failed: ' + $_.Exception.Message)
    Write-Note 'Common causes: missing transitive .dll, or .NET Framework 4.x not installed.'
    exit 1
}
Write-Ok 'All assemblies loaded into the .NET AppDomain'

# ---------------------------------------------------------------------------
# 4. Construct UserPasswordCredentials + WebServiceManager (sign in)
# ---------------------------------------------------------------------------

Write-Step 4 'Sign in via UserPasswordCredentials + WebServiceManager'
# Vault 2025 SDK changed the signature: the first arg is now a
# ServerIdentities object (data + file server hostnames) rather than
# a single hostname string. For typical single-server installs both
# are the same hostname.
$identities = New-Object Autodesk.Connectivity.WebServices.ServerIdentities
$identities.DataServer = $serverHost
$identities.FileServer = $serverHost

# Try each LicensingAgent in turn until one of them is accepted by the
# server. Different Vault editions / user permission combos accept
# different agents — automation accounts usually want None or Server.
$mgr = $null
$cred = $null
$lastError = ''
foreach ($agent in @('None', 'Server', 'Client', 'Token')) {
    try {
        $la = [Autodesk.Connectivity.WebServices.LicensingAgent]::$agent
        $cred = New-Object Autodesk.Connectivity.WebServicesTools.UserPasswordCredentials(
            $identities, $db, $user, $pass, $la
        )
        $mgr = New-Object Autodesk.Connectivity.WebServicesTools.WebServiceManager($cred)
        Write-Ok ('Sign-in OK with LicensingAgent=' + $agent)
        break
    } catch {
        $lastError = $_.Exception.Message
        Write-Note ('LicensingAgent=' + $agent + ' rejected: ' + ($lastError.Split([Environment]::NewLine) | Select-Object -First 1))
        $mgr = $null
    }
}
if ($null -eq $mgr) {
    Write-Fail ('All LicensingAgent variants rejected. Last error: ' + $lastError)
    Write-Note 'If you see VaultLicenseException, the user account probably has no'
    Write-Note 'Vault license assigned. A Vault admin can fix this in Vault Server'
    Write-Note 'Console -> Tools -> Administration -> Users.'
    exit 1
}

# ---------------------------------------------------------------------------
# 5. Read every lifecycle definition + state name
# ---------------------------------------------------------------------------

Write-Step 5 'Call LifeCycleService.GetAllLifeCycleDefinitions'
try {
    $defs = $mgr.LifeCycleService.GetAllLifeCycleDefinitions()
} catch {
    Write-Fail ('GetAllLifeCycleDefinitions failed: ' + $_.Exception.Message)
    exit 1
}
$nDefs = if ($defs) { $defs.Count } else { 0 }
Write-Ok ('Returned ' + $nDefs + ' lifecycle definition(s)')

$stateNames = New-Object System.Collections.Generic.HashSet[string]
foreach ($d in $defs) {
    Write-Note ('- ' + $d.DispName + '  id=' + $d.Id)
    foreach ($s in $d.StateArray) {
        $name = if ($s.DispName) { $s.DispName } else { $s.Name }
        if ($name) { $null = $stateNames.Add([string]$name) }
        Write-Note ('    state: ' + $name + '  id=' + $s.Id)
    }
}
$sortedStates = ($stateNames | Sort-Object) -join ', '
Write-Note ('Distinct state names: [' + $sortedStates + ']')

# ---------------------------------------------------------------------------
# 6. Read property definitions for ITEM
# ---------------------------------------------------------------------------

Write-Step 6 'Call PropertyService.GetPropertyDefinitionInfosByEntityClassId ITEM'
$propInfos = $null
try {
    $propInfos = $mgr.PropertyService.GetPropertyDefinitionInfosByEntityClassId('ITEM', $null)
} catch {
    try {
        $propInfos = $mgr.PropertyService.GetPropertyDefinitionsByEntityClassId('ITEM')
    } catch {
        Write-Fail ('PropertyService call failed: ' + $_.Exception.Message)
        exit 1
    }
}
$nProps = if ($propInfos) { $propInfos.Count } else { 0 }
Write-Ok ('Returned ' + $nProps + ' property definition(s) for ITEM entity')

$count = 0
foreach ($p in $propInfos) {
    $pdef = if ($p.PropDef) { $p.PropDef } else { $p }
    $disp = if ($pdef.DispName) { $pdef.DispName } else { $pdef.Name }
    Write-Note ('    ' + $disp + '  sysName=' + $pdef.SysName + '  id=' + $pdef.Id)
    $count++
    if ($count -ge 8) { break }
}
if ($nProps -gt 8) {
    Write-Note ('    ... and ' + ($nProps - 8) + ' more')
}

# ---------------------------------------------------------------------------
# 7. Read item categories
# ---------------------------------------------------------------------------

Write-Step 7 'Call CategoryService.GetCategoriesByEntityClassId ITEM'
try {
    $cats = $mgr.CategoryService.GetCategoriesByEntityClassId('ITEM', $true)
} catch {
    Write-Fail ('GetCategoriesByEntityClassId failed: ' + $_.Exception.Message)
    exit 1
}
$nCats = if ($cats) { $cats.Count } else { 0 }
Write-Ok ('Returned ' + $nCats + ' item category(ies)')
foreach ($c in $cats) {
    Write-Note ('    ' + $c.Name + '  id=' + $c.Id)
}

# ---------------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------------

Write-Step 8 'Summary'
Write-Ok 'ALL CHECKS PASSED -- Vault SDK is reachable from PowerShell.'
Write-Note ''
Write-Note 'What this means:'
Write-Note '  - The full SOAP API is available on this server through the SDK,'
Write-Note '    even though the raw .asmx URLs are not directly reachable.'
Write-Note ''
Write-Note 'Two viable next steps:'
Write-Note '  A) Wrap the SDK in PowerShell, call it from Python via subprocess.'
Write-Note '     Zero Python deps; works on Python 3.14 today.'
Write-Note '  B) Get pythonnet 3.x running on Python 3.13. Cleaner long term.'
