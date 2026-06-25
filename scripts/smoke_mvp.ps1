param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Source = "github",
    [string]$Account = "octocat",
    [string]$AccessToken = "",
    [int]$MaxResults = 10
)

$ErrorActionPreference = "Stop"

$email = "smoke-$([Guid]::NewGuid().ToString('N').Substring(0, 8))@shadowself.local"

$body = @{
    email = $email
    username = "smoke_user"
}

$ingestPath = "/ingest/github"
if ($Source -eq "github") {
    $body.github_username = $Account
    $ingestPath = "/ingest/github"
} elseif ($Source -eq "reddit") {
    $body.reddit_username = $Account
    $ingestPath = "/ingest/reddit-live"
} elseif ($Source -in @("gmail", "linkedin", "meta")) {
    if (-not $AccessToken) {
        throw "-AccessToken is required for $Source ingestion"
    }
    $body.access_token = $AccessToken
    $body.max_results = $MaxResults
    $ingestPath = "/ingest/$Source"
} else {
    $body.text = "I am excited to build useful tools with my team. I plan my work carefully and enjoy progress. Sometimes deadlines create stress and anxiety, but I keep learning and asking better questions."
    $body.source_name = "smoke_test"
    $ingestPath = "/ingest/manual"
}

$payload = $body | ConvertTo-Json

Write-Host "Checking health..."
$health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health"
$health | ConvertTo-Json

Write-Host "Ingesting $Source source..."
$ingest = Invoke-RestMethod -Method Post -Uri "$BaseUrl$ingestPath" -ContentType "application/json" -Body $payload
$ingest | ConvertTo-Json

Write-Host "Analyzing profile..."
$analysis = Invoke-RestMethod -Method Post -Uri "$BaseUrl/analyze/$($ingest.user_id)"
$analysis | ConvertTo-Json

Write-Host "Loading profile..."
$profile = Invoke-RestMethod -Method Get -Uri "$BaseUrl/profile/$($ingest.user_id)"
$profile | ConvertTo-Json -Depth 8
