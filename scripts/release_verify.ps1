param(
    [string]$Python = "py -3.11",
    [int]$Port = 5000,
    [int]$HealthRetries = 15,
    [int]$HealthDelaySeconds = 2
)

$ErrorActionPreference = "Stop"

Write-Host "[release_verify] 1) compile Python sources"
Invoke-Expression "$Python -m compileall -q run.py app tests"

Write-Host "[release_verify] 2) validate Docker Compose config"
docker compose config --quiet

Write-Host "[release_verify] 3) build web image"
docker compose build web

Write-Host "[release_verify] 4) start Postgres and Redis"
docker compose up -d postgres redis

Write-Host "[release_verify] 5) apply migrations"
docker compose run --rm web flask db upgrade

Write-Host "[release_verify] 6) start web"
docker compose up -d web

Write-Host "[release_verify] 7) verify HTTP response"
$response = $null
for ($attempt = 1; $attempt -le $HealthRetries; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://localhost:$Port/"
        if ($response.StatusCode -eq 200) {
            break
        }
    } catch {
        if ($attempt -eq $HealthRetries) {
            throw
        }
        Start-Sleep -Seconds $HealthDelaySeconds
        continue
    }
    if ($attempt -lt $HealthRetries) {
        Start-Sleep -Seconds $HealthDelaySeconds
    }
}

if (-not $response -or $response.StatusCode -ne 200) {
    throw "Expected HTTP 200 from http://localhost:$Port/ but got $($response.StatusCode)"
}

Write-Host "[release_verify] completed successfully"
