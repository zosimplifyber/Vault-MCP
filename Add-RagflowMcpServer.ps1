<#
.SYNOPSIS
    Adds (or updates) the "ragflow" entry inside the mcpServers block of
    claude_desktop_config.json.

.EXAMPLE
    .\Add-RagflowMcpServer.ps1
    .\Add-RagflowMcpServer.ps1 -DryRun
    .\Add-RagflowMcpServer.ps1 -Url http://127.0.0.1:9382/mcp -ServerName ragflow
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'),
    [string]$ServerName = 'ragflow',
    [string]$Url        = 'http://127.0.0.1:9382/mcp',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ---- the entry we want to end up with -------------------------------------
$entry = [ordered]@{
    command = 'npx'
    args    = @('-y', 'mcp-remote', $Url, '--transport', 'http-only')
}

# ---- load (or create) the config ------------------------------------------
if (Test-Path -LiteralPath $ConfigPath) {
    $raw = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8

    if ([string]::IsNullOrWhiteSpace($raw)) {
        $config = [pscustomobject]@{}
    }
    else {
        try {
            $config = $raw | ConvertFrom-Json
        }
        catch {
            throw "'$ConfigPath' is not valid JSON - fix it by hand first. ($($_.Exception.Message))"
        }
    }

    # back up before touching anything
    if (-not $DryRun) {
        $backup = "$ConfigPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $ConfigPath -Destination $backup
        Write-Host "Backup written to $backup" -ForegroundColor DarkGray
    }
}
else {
    Write-Host "No config at $ConfigPath - creating a new one." -ForegroundColor Yellow
    $parent = Split-Path -Parent $ConfigPath
    if (-not $DryRun -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $config = [pscustomobject]@{}
}

if ($null -eq $config) { $config = [pscustomobject]@{} }

# ---- make sure mcpServers exists ------------------------------------------
if ($config.PSObject.Properties.Name -notcontains 'mcpServers' -or $null -eq $config.mcpServers) {
    $config | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue ([pscustomobject]@{}) -Force
}

# ---- add or replace the server --------------------------------------------
if ($config.mcpServers.PSObject.Properties.Name -contains $ServerName) {
    Write-Host "'$ServerName' already exists - replacing it. Previous value:" -ForegroundColor Yellow
    $config.mcpServers.$ServerName | ConvertTo-Json -Depth 20 | Write-Host -ForegroundColor DarkGray
}
$config.mcpServers | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([pscustomobject]$entry) -Force

# ---- write it back (UTF-8, no BOM) ----------------------------------------
$json = $config | ConvertTo-Json -Depth 20

if ($DryRun) {
    Write-Host "`n--- DRY RUN, nothing written ---`n" -ForegroundColor Cyan
    Write-Host $json
    return
}

[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))

# ---- verify ---------------------------------------------------------------
$check = (Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).mcpServers.$ServerName
if ($null -eq $check) { throw "Write-back verification failed - '$ServerName' not found in $ConfigPath" }

Write-Host "`nUpdated $ConfigPath" -ForegroundColor Green
$check | ConvertTo-Json -Depth 20 | Write-Host
Write-Host "`nRestart Claude Desktop (quit from the tray, not just close the window) to pick it up." -ForegroundColor Cyan
