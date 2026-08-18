<#
.SYNOPSIS
    Blocks until RAGFlow can actually serve MCP tools, so clients aren't started
    against a half-booted stack.

.DESCRIPTION
    A port check on 9382 is not enough - the port binds roughly 30s before the
    server can answer tools/list, because building the tool descriptions makes
    the MCP server call the API backend on 9380 for the dataset list. A client
    that connects in that window gets a tool list with no tools and does not
    retry.

    So readiness here means a real initialize + tools/list handshake that comes
    back with at least one tool.

.EXAMPLE
    .\Wait-RagflowReady.ps1
    .\Wait-RagflowReady.ps1 -TimeoutSeconds 600
    .\Wait-RagflowReady.ps1 -Url http://127.0.0.1:9382/mcp
#>
[CmdletBinding()]
param(
    [string]$Url            = 'http://127.0.0.1:9382/mcp',
    [int]   $TimeoutSeconds = 300,
    [int]   $PollSeconds    = 5
)

$ErrorActionPreference = 'Stop'

$headers = @{
    'Content-Type' = 'application/json'
    'Accept'       = 'application/json, text/event-stream'
}

$initBody = @{
    jsonrpc = '2.0'
    id      = 0
    method  = 'initialize'
    params  = @{
        protocolVersion = '2024-11-05'
        capabilities    = @{}
        clientInfo      = @{ name = 'readiness-probe'; version = '1' }
    }
} | ConvertTo-Json -Depth 10 -Compress

$toolsBody = @{
    jsonrpc = '2.0'
    id      = 1
    method  = 'tools/list'
    params  = @{}
} | ConvertTo-Json -Depth 10 -Compress

# Returns the tool count, or -1 when the stack isn't serving yet.
function Test-RagflowReady {
    try {
        $init = Invoke-RestMethod -Method Post -Uri $Url -Headers $headers -Body $initBody -TimeoutSec 15
        if ($null -eq $init.result.serverInfo) { return -1 }

        $tools = Invoke-RestMethod -Method Post -Uri $Url -Headers $headers -Body $toolsBody -TimeoutSec 30

        # The failure mode we're gating on: a well-formed reply carrying no tools.
        if ($null -eq $tools.result -or $null -eq $tools.result.tools) { return -1 }
        return @($tools.result.tools).Count
    }
    catch {
        return -1
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$attempt  = 0

Write-Host "Waiting for RAGFlow MCP at $Url ..." -ForegroundColor Cyan

while ((Get-Date) -lt $deadline) {
    $attempt++
    $count = Test-RagflowReady

    if ($count -gt 0) {
        $waited = [int]((Get-Date) - $deadline.AddSeconds(-$TimeoutSeconds)).TotalSeconds
        Write-Host "RAGFlow is ready - $count tool(s) served after ${waited}s / $attempt attempt(s)." -ForegroundColor Green
        Write-Host "Safe to start Claude Code / Claude Desktop now." -ForegroundColor Green
        exit 0
    }

    Write-Host ("  attempt {0}: not ready yet, retrying in {1}s" -f $attempt, $PollSeconds) -ForegroundColor DarkGray
    Start-Sleep -Seconds $PollSeconds
}

Write-Host "RAGFlow did not become ready within $TimeoutSeconds seconds." -ForegroundColor Red
Write-Host "Check the stack with: docker logs --tail 50 docker-ragflow-cpu-1" -ForegroundColor Yellow
exit 1
