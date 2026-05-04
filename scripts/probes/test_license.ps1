# Vault license-acquisition test
# -------------------------------
# Walks every license / auth path the SDK exposes and reports which ones
# this machine + this user account can actually use. Read-only -- none of
# the calls modify Vault data.
#
# Three layers are probed:
#
#   1. LicensingAgent (None / Client / Server / Token) on the legacy
#      WebServiceManager + UserPasswordCredentials path. This is what
#      probe_edition.ps1 and probe_permissions.ps1 use.
#
#   2. AuthenticationFlags (Standard / Standard|ServerLicense /
#      WindowsAuthenticationWithCredentials / WindowsAuthentication) on
#      the high-level VDF ConnectionManager.LogIn path. This is what
#      vault_sdk.ps1 uses for writes.
#
#   3. Interactive Autodesk Account / SSO sign-in via the standard Vault
#      login dialog (VdfForms.Library.Login). Pops a UI on the desktop --
#      pick the "Autodesk Account" option and complete the browser flow.
#      Reports back the AuthenticationFlags / license type that the
#      cloud sign-in actually granted on this server.
#
# Each attempt prints PASS / FAIL with the underlying error message so
# you can tell whether the failure is "no seat available", "wrong
# credentials", "license service unreachable", or something else.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_license.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_license.ps1 -SkipInteractive

param(
    [switch]$SkipInteractive
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Header($text) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host $text -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor Cyan
}
function Write-Pass($text) { Write-Host "  PASS  $text" -ForegroundColor Green }
function Write-Fail($text) { Write-Host "  FAIL  $text" -ForegroundColor Red }
function Write-Note($text) { Write-Host "        $text" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------
# 0. Config + SDK load
# ---------------------------------------------------------------------------
$configPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
if (-not (Test-Path $configPath)) {
    Write-Fail "config.json not found at $configPath"
    exit 1
}
$cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$serverHost = ($cfg.vault.servername -replace '^https?://', '' -replace '/$', '')

Write-Header 'Environment'
Write-Note "Server:   $serverHost"
Write-Note "Database: $($cfg.vault.database)"
Write-Note "User:     $($cfg.vault.username)"

$SdkBin = if ($env:VAULT_SDK_BIN) { $env:VAULT_SDK_BIN } else {
    'C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64'
}
Write-Note "SDK bin:  $SdkBin"

# Per AU SD321955 page 14: PowerShell needs the Autodesk Licensing native
# DLL on its DLL search path before any license-acquiring SDK call. The PDF
# says copy AdskLicensingSDK_2.dll to System32\WindowsPowerShell\v1.0; on
# Vault 2025 the file is renamed to AdskLicensingSDK_8.dll. Rather than
# modifying a system folder, prepend whichever folder ships the DLL to
# $env:PATH for this process only.
$licDllNames = @('AdskLicensingSDK_8.dll','AdskLicensingSDK_2.dll')
$licSearchDirs = @(
    $SdkBin,
    'C:\Program Files\Autodesk\Vault Client 2025\Explorer',
    'C:\Program Files\Autodesk\Inventor 2025\Bin'
)
$licDirAdded = $null
foreach ($d in $licSearchDirs) {
    foreach ($n in $licDllNames) {
        if (Test-Path (Join-Path $d $n)) {
            if ($env:PATH -notlike "*$d*") {
                $env:PATH = "$d;$env:PATH"
            }
            $licDirAdded = "$d ($n)"
            break
        }
    }
    if ($licDirAdded) { break }
}
if ($licDirAdded) {
    Write-Note "Licensing DLL on PATH: $licDirAdded"
} else {
    Write-Note "Licensing DLL: NOT FOUND in any known location -- license calls will fail."
}

$assemblies = @(
    'Autodesk.Connectivity.WebServices.dll',
    'Autodesk.DataManagement.Client.Framework.dll',
    'Autodesk.DataManagement.Client.Framework.Vault.dll'
)
foreach ($n in $assemblies) {
    $p = Join-Path $SdkBin $n
    if (-not (Test-Path $p)) {
        Write-Fail "SDK assembly missing: $p"
        Write-Note 'Install / locate the Autodesk Vault SDK and set $env:VAULT_SDK_BIN if needed.'
        exit 1
    }
    Add-Type -Path $p
}
Write-Pass 'SDK assemblies loaded'

# ---------------------------------------------------------------------------
# 1. Local Autodesk Licensing service / Vault Client install
# ---------------------------------------------------------------------------
Write-Header '1. Local Vault Client / Autodesk Licensing'

$vaultClientGlob = 'C:\Program Files\Autodesk\Vault*'
$installs = Get-ChildItem -Path $vaultClientGlob -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch 'SDK' }
if ($installs) {
    foreach ($i in $installs) { Write-Pass "Vault Client present: $($i.FullName)" }
} else {
    Write-Fail "No Vault Client install found under C:\Program Files\Autodesk\Vault*"
    Write-Note 'Client license mode requires a Vault Client install on this machine.'
}

$licSvc = Get-Service -Name 'AdskLicensingService' -ErrorAction SilentlyContinue
if ($licSvc) {
    if ($licSvc.Status -eq 'Running') {
        Write-Pass "AdskLicensingService is Running"
    } else {
        Write-Fail "AdskLicensingService status: $($licSvc.Status)"
        Write-Note 'Start it from services.msc, then re-run this script.'
    }
} else {
    Write-Fail 'AdskLicensingService not installed (Autodesk Licensing v2 missing)'
}

# Try to detect whether this user is already signed into Vault Explorer
# (the most common cause of "Failed to acquire a license" with Standard).
$explorerProcs = Get-Process -Name 'Connectivity.VaultPro','Connectivity.VaultWG','Connectivity.VaultBasic' -ErrorAction SilentlyContinue
if ($explorerProcs) {
    foreach ($p in $explorerProcs) {
        Write-Note "Vault Explorer running: $($p.ProcessName) (PID $($p.Id))"
    }
    Write-Note 'If signed in as the same user, Standard license mode WILL fail with VaultLicenseException.'
} else {
    Write-Pass 'No Vault Explorer process detected (good for Standard / Client mode)'
}

# ---------------------------------------------------------------------------
# 2. LicensingAgent matrix (legacy WebServiceManager path)
# ---------------------------------------------------------------------------
Write-Header '2. LicensingAgent (legacy WebServiceManager + UserPasswordCredentials)'

$identities = New-Object Autodesk.Connectivity.WebServices.ServerIdentities
$identities.DataServer = $serverHost
$identities.FileServer = $serverHost

foreach ($agentName in @('None','Client','Server','Token')) {
    try {
        $agent = [Autodesk.Connectivity.WebServices.LicensingAgent]::$agentName
    } catch {
        Write-Fail "$agentName : enum value not present in this SDK"
        continue
    }
    try {
        $cred = New-Object Autodesk.Connectivity.WebServicesTools.UserPasswordCredentials(
            $identities, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password, $agent
        )
        $mgr = New-Object Autodesk.Connectivity.WebServicesTools.WebServiceManager($cred)
        # GetServerName forces the credential to be exercised, not just constructed.
        $serverName = $mgr.InformationService.GetServerName()
        Write-Pass "LicensingAgent::$agentName  -> server '$serverName'"
        try { $mgr.Dispose() } catch { }
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) { $msg += " | inner: $($_.Exception.InnerException.Message)" }
        $first = ($msg -split "`n" | Select-Object -First 1)
        Write-Fail "LicensingAgent::$agentName  -> $first"
    }
}

# ---------------------------------------------------------------------------
# 3. AuthenticationFlags matrix (VDF ConnectionManager.LogIn path)
# ---------------------------------------------------------------------------
Write-Header '3. AuthenticationFlags (VDF ConnectionManager.LogIn -- used for writes)'

$afType = [Autodesk.DataManagement.Client.Framework.Vault.Currency.Connections.AuthenticationFlags]
$cm = [Autodesk.DataManagement.Client.Framework.Vault.Library]::ConnectionManager

$flagCombos = @(
    @{ name = 'ReadOnly';                            flags = $afType::ReadOnly },
    @{ name = 'Standard';                            flags = $afType::Standard },
    @{ name = 'Standard|ServerLicense';              flags = ($afType::Standard -bor $afType::ServerLicense) },
    @{ name = 'WindowsAuthenticationWithCredentials';flags = $afType::WindowsAuthenticationWithCredentials },
    @{ name = 'WindowsAuthentication';               flags = $afType::WindowsAuthentication }
)

foreach ($c in $flagCombos) {
    try {
        $r = $cm.LogIn($serverHost, $cfg.vault.database, $cfg.vault.username, $cfg.vault.password, $c.flags, $null)
        if ($r -and $r.Success) {
            Write-Pass ("{0}  -> success (ActualFlags={1})" -f $c.name, $r.Connection.AuthenticationFlags)
            try { $cm.LogOut($r.Connection) | Out-Null } catch { }
        } else {
            $errs = @()
            if ($r -and $r.ErrorMessages) {
                foreach ($kv in $r.ErrorMessages.GetEnumerator()) {
                    $errs += ('{0}: {1}' -f $kv.Key, $kv.Value)
                }
            }
            $detail = if ($errs.Count) { ($errs -join ' / ') } else { 'no Success, no error messages' }
            Write-Fail ("{0}  -> {1}" -f $c.name, $detail)
        }
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) { $msg += " | inner: $($_.Exception.InnerException.Message)" }
        $first = ($msg -split "`n" | Select-Object -First 1)
        Write-Fail ("{0}  threw: {1}" -f $c.name, $first)
    }
}

# ---------------------------------------------------------------------------
# 4. Interactive Autodesk Account / SSO sign-in via VDF login dialog
# ---------------------------------------------------------------------------
Write-Header '4. Autodesk Account / SSO sign-in (interactive -- opens a dialog)'

if ($SkipInteractive) {
    Write-Note 'Skipped (--SkipInteractive).'
} else {
    Write-Note ("PowerShell host : {0}" -f $PSVersionTable.PSVersion)
    Write-Note ("CLR runtime     : {0}" -f [System.Environment]::Version)
    $apt = [System.Threading.Thread]::CurrentThread.ApartmentState
    Write-Note ("Apartment       : {0}" -f $apt)
    if ($apt -ne 'STA') {
        Write-Note 'WinForms dialogs require STA threading. The login dialog may fail to'
        Write-Note 'render or hang in MTA. Re-run with the -STA flag:'
        Write-Note '  powershell -STA -NoProfile -ExecutionPolicy Bypass -File scripts/test_license.ps1'
    }

    # The Forms DLL hosts the VDF login dialog (the same one Vault Explorer
    # shows). It depends on a chain of DevExpress + Vault assemblies that
    # all live in the Vault Client "Explorer" folder, so loading from the
    # SDK bin tends to break.
    #
    # Pin to the Vault Client whose version matches the SDK we loaded in
    # sections 1-3. Mixing assembly versions across years (e.g. 2025 SDK
    # + 2027 Forms) causes type-identity mismatches and, on PS 5.1, a
    # cascade of "System.Runtime 10.0.0.0 not found" loader errors because
    # the 2027 client is built against .NET 10.
    $sdkVersion = $null
    if ($SdkBin -match 'Autodesk Vault (\d{4}) SDK') { $sdkVersion = $matches[1] }
    $sdkVersionDisplay = if ($sdkVersion) { $sdkVersion } else { '(could not parse from path)' }
    Write-Note ("SDK version    : {0}" -f $sdkVersionDisplay)

    $formsCandidates = @()
    $clientRoots = Get-ChildItem 'C:\Program Files\Autodesk' -Directory -Filter 'Vault Client*' -ErrorAction SilentlyContinue
    if ($sdkVersion) {
        # Strict match: same year as the SDK.
        $matched = $clientRoots | Where-Object { $_.Name -match ('Vault Client\s*' + [regex]::Escape($sdkVersion) + '\b') }
        foreach ($root in $matched) {
            $p = Join-Path $root.FullName 'Explorer\Autodesk.DataManagement.Client.Framework.Vault.Forms.dll'
            if (Test-Path $p) { $formsCandidates += $p }
        }
    }
    if ($formsCandidates.Count -eq 0) {
        # No exact version match -- fall back to ascending order (older
        # is more likely .NET Framework compatible with PS 5.1).
        Write-Note 'No exact-version Vault Client found; falling back to oldest available.'
        foreach ($root in ($clientRoots | Sort-Object Name)) {
            $p = Join-Path $root.FullName 'Explorer\Autodesk.DataManagement.Client.Framework.Vault.Forms.dll'
            if (Test-Path $p) { $formsCandidates += $p }
        }
    }
    $sdkForms = Join-Path $SdkBin 'Autodesk.DataManagement.Client.Framework.Vault.Forms.dll'
    if ((Test-Path $sdkForms) -and ($formsCandidates -notcontains $sdkForms)) {
        $formsCandidates += $sdkForms
    }

    if ($formsCandidates.Count -eq 0) {
        Write-Fail 'VDF Forms DLL not found in any Vault Client install or SDK path.'
        Write-Note 'Cannot test Autodesk Account sign-in without the Forms assembly.'
        # Skip the rest of section 4 -- the trailing closing braces below still match.
        $loadedFormsDll = $null
    } else {
        # One AssemblyResolve handler covers every candidate's Explorer folder.
        # CRITICAL: cache results per assembly name to prevent infinite
        # recursion. Without the cache, when the dialog loads a transitive
        # dep that is missing on this machine, the handler returns null,
        # .NET re-fires AssemblyResolve, and we loop until StackOverflow.
        # The dictionary doubles as a re-entry guard: an entry of $null
        # means "already tried, give up immediately."
        $script:resolveDirs = New-Object System.Collections.ArrayList
        foreach ($p in $formsCandidates) {
            $d = Split-Path -Parent $p
            if (-not $script:resolveDirs.Contains($d)) { [void]$script:resolveDirs.Add($d) }
        }
        $script:resolveCache = @{}
        $resolveHandler = {
            param($sender, $eventArgs)
            $name = ($eventArgs.Name -split ',')[0].Trim()
            if ($script:resolveCache.ContainsKey($name)) {
                return $script:resolveCache[$name]
            }
            # Mark as in-progress so a nested resolve for the same name
            # short-circuits to null instead of re-entering this handler.
            $script:resolveCache[$name] = $null
            foreach ($dir in $script:resolveDirs) {
                $cand = Join-Path $dir ($name + '.dll')
                if (Test-Path $cand) {
                    try {
                        $asm = [System.Reflection.Assembly]::LoadFrom($cand)
                        $script:resolveCache[$name] = $asm
                        return $asm
                    } catch { }
                }
            }
            return $null
        }
        [System.AppDomain]::CurrentDomain.add_AssemblyResolve($resolveHandler)

        $loadedFormsDll = $null
        foreach ($formsDll in $formsCandidates) {
            Write-Note "Trying Forms DLL: $formsDll"
            try {
                Add-Type -Path $formsDll -ErrorAction Stop
                $loadedFormsDll = $formsDll
                Write-Pass "Loaded successfully"
                break
            } catch {
                # Dedupe loader exceptions -- Add-Type often emits 100+ identical
                # "Could not load System.Runtime 10.0.0.0" lines on a .NET version
                # mismatch. Show the first 4 unique messages.
                $loaderMsgs = @()
                $ex = $_.Exception
                while ($ex) {
                    if ($ex.PSObject.Properties.Name -contains 'LoaderExceptions' -and $ex.LoaderExceptions) {
                        foreach ($le in $ex.LoaderExceptions) {
                            if ($le -and $le.Message) { $loaderMsgs += $le.Message }
                        }
                        break
                    }
                    $ex = $ex.InnerException
                }
                $unique = @($loaderMsgs | Select-Object -Unique | Select-Object -First 4)
                Write-Fail ("Did not load: {0}" -f $_.Exception.Message)
                foreach ($u in $unique) { Write-Note "  needs: $u" }
            }
        }

        if (-not $loadedFormsDll) {
            Write-Fail 'Every Forms DLL candidate failed to load.'
            if ($PSVersionTable.PSVersion.Major -lt 7) {
                Write-Note 'You are running Windows PowerShell 5.x on .NET Framework. Vault Client'
                Write-Note '2026+ ships .NET 8 / .NET 10 assemblies that cannot load in .NET Framework.'
                Write-Note 'Re-run this script under PowerShell 7+ to test those builds:'
                Write-Note '  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/test_license.ps1'
                Write-Note ''
                Write-Note 'If only the 2025 Vault Client failed to load, that is a different issue --'
                Write-Note 'check that the 2025 Explorer folder is intact.'
            } else {
                Write-Note 'PowerShell 7+ in use but loader still failed -- confirm the .NET runtime'
                Write-Note ("(currently {0}) matches the Vault Client build's TargetFramework." -f [System.Environment]::Version)
            }
            [System.AppDomain]::CurrentDomain.remove_AssemblyResolve($resolveHandler)
        }
    }

    # Only proceed to the dialog if Forms DLL actually loaded.
    if ($loadedFormsDll) {
        try {
            $settingsType = [Autodesk.DataManagement.Client.Framework.Vault.Forms.Settings.LoginSettings]
            $settings = New-Object $settingsType

            # Pre-fill what we know so the dialog opens on the right server/db.
            foreach ($prop in @('ServerName','Server','VaultName','Vault','UserName','User')) {
                if ($settings.GetType().GetProperty($prop)) {
                    try {
                        switch ($prop) {
                            { $_ -in 'ServerName','Server' }    { $settings.$prop = $serverHost }
                            { $_ -in 'VaultName','Vault' }      { $settings.$prop = $cfg.vault.database }
                            { $_ -in 'UserName','User' }        { $settings.$prop = $cfg.vault.username }
                        }
                    } catch { }
                }
            }

            # Use the PDF-recommended pattern (AU class SD321955, section 2.4.6):
            # AutoLoginMode = RestoreAndExecute. This reuses encrypted credentials
            # stored from a prior Vault Explorer / Autodesk Account sign-in,
            # and only pops the dialog the first time (or if stored creds are
            # invalid). Subsequent runs re-use the same Autodesk Account silently.
            $autoModeProp = $settings.GetType().GetProperty('AutoLoginMode')
            if ($autoModeProp) {
                $enumType = $autoModeProp.PropertyType
                foreach ($candidate in @('RestoreAndExecute','RestoreLastUsedConnection','AskForCredentials')) {
                    try {
                        $val = [System.Enum]::Parse($enumType, $candidate)
                        $autoModeProp.SetValue($settings, $val, $null)
                        Write-Note ("AutoLoginMode  : {0}" -f $candidate)
                        break
                    } catch { }
                }
            }

            Write-Note 'Calling VdfForms.Library.Login (uses stored Autodesk Account creds if any)...'

            # Run Login on a brand-new STA thread we control. PowerShell's
            # main thread, even with -STA, has accumulated state from earlier
            # SDK calls in this script (and from PowerShell's own runspace
            # initialization) that has historically corrupted WinForms init
            # and surfaced as StackOverflowException inside Library.Login.
            # A fresh thread + clean WinForms init avoids that.
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing

            $bag = [hashtable]::Synchronized(@{
                Settings   = $settings
                Connection = $null
                Error      = $null
            })

            $worker = [System.Threading.Thread]::new(
                [System.Threading.ParameterizedThreadStart]{
                    param($state)
                    try {
                        [System.Windows.Forms.Application]::EnableVisualStyles()
                        [System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)
                        $loginType = [Autodesk.DataManagement.Client.Framework.Vault.Forms.Library]
                        $state.Connection = $loginType::Login($state.Settings)
                    } catch {
                        $state.Error = $_.Exception.ToString()
                    }
                }
            )
            $worker.SetApartmentState([System.Threading.ApartmentState]::STA)
            $worker.IsBackground = $true
            $worker.Start($bag)
            # Cap the wait so a hung dialog can't hold the script forever.
            $finished = $worker.Join([System.TimeSpan]::FromMinutes(3))
            if (-not $finished) {
                Write-Fail 'Login worker thread did not return within 3 minutes -- aborting.'
                try { $worker.Abort() } catch { }
            }
            if ($bag.Error) {
                throw ([System.Exception]::new("Login worker raised: " + $bag.Error))
            }
            $conn = $bag.Connection

            if ($null -eq $conn) {
                Write-Fail 'Dialog cancelled or returned no connection.'
            } else {
                Write-Pass 'Sign-in successful via Autodesk Account / Vault dialog'

                # Walk a generous list of properties -- exact names vary by SDK.
                $report = [ordered]@{}
                foreach ($prop in @(
                    'Server','ServerName','Vault','VaultName','UserName','UserId',
                    'AuthenticationFlags','LicenseType','IsConnected','Ticket'
                )) {
                    try {
                        $v = $conn.$prop
                        if ($null -ne $v) { $report[$prop] = $v }
                    } catch { }
                }
                foreach ($k in $report.Keys) {
                    Write-Note ("{0,-22} {1}" -f ($k + ':'), $report[$k])
                }

                # Try to release the seat we just consumed.
                $loggedOut = $false
                foreach ($method in @('LogOff','LogOut')) {
                    if ($loginType.GetMethod($method, [type[]]@($conn.GetType()))) {
                        try {
                            $loginType::$method($conn) | Out-Null
                            $loggedOut = $true
                            break
                        } catch { }
                    }
                }
                if (-not $loggedOut) {
                    try {
                        [Autodesk.DataManagement.Client.Framework.Vault.Library]::ConnectionManager.LogOut($conn) | Out-Null
                        $loggedOut = $true
                    } catch { }
                }
                if ($loggedOut) {
                    Write-Note 'Seat released (logged off).'
                } else {
                    Write-Note 'Could not auto-logout -- close this PowerShell session to release the seat.'
                }
            }
        } catch {
            $msg = $_.Exception.Message
            if ($_.Exception.InnerException) { $msg += " | inner: $($_.Exception.InnerException.Message)" }
            Write-Fail ("Autodesk Account sign-in path threw: " + $msg)
        } finally {
            [System.AppDomain]::CurrentDomain.remove_AssemblyResolve($resolveHandler)
        }
    }
}

# ---------------------------------------------------------------------------
# 5. Recommendation
# ---------------------------------------------------------------------------
Write-Header '5. Recommendation'
Write-Note 'Look for a PASS row in section 3 -- that is the auth flag combo'
Write-Note 'vault_sdk.ps1 should use. If only "Standard|ServerLicense" passes,'
Write-Note 'this machine has a network/server Vault license (not a single-user'
Write-Note 'Client seat) and vault_sdk.ps1 needs ServerLicense restored as a'
Write-Note 'fallback. If "Standard" passes, Client mode is working.'
Write-Note ''
Write-Note 'In section 2, "None" should always pass (read-only, no seat).'
Write-Note 'If "None" fails, the credentials or server URL are wrong -- the'
Write-Note 'license is not the issue.'
Write-Note ''
Write-Note 'Section 4 is the Autodesk Account / SSO path. If sections 2 and 3'
Write-Note 'all FAIL but section 4 PASSes, your Vault server is configured for'
Write-Note 'cloud-only sign-in and the SDK scripts need to use the dialog flow'
Write-Note '(VdfForms.Library.Login) instead of UserPasswordCredentials.'
