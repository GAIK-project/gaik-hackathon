<#
.SYNOPSIS
  Runs the WhatsApp bridge with keys loaded into this process only.

.DESCRIPTION
  Reads named keys from the owner's personal env library with dotenvx (one key at a time,
  up to four tries each; an empty value or one that starts with "encrypted:" is refused),
  reads HACK_BRIDGE_KEY from the repo .env, and starts main.py. No value is printed or
  written to disk, and the variables are removed from the session when the run ends.

.EXAMPLE
  .\run.ps1 -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi
  .\run.ps1 -Status
  .\run.ps1 -Stop -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi
#>
[CmdletBinding()]
param(
  # The app's public origin: the bridge calls it, and Meta's webhook is pointed at it.
  [string]$PublicBaseUrl,
  # Detach the webhook (and end the session when -PublicBaseUrl is given), then exit.
  [switch]$Stop,
  # Read-only: can the token read the number and the account. Sends and changes nothing.
  [switch]$Status,
  # Stop and detach after this many minutes (0: run until Ctrl+C).
  [double]$Minutes = 180
)

$ErrorActionPreference = 'Stop'
$library = Join-Path $HOME '.agents\env\personal'
$repoEnv = Join-Path (Split-Path $PSScriptRoot -Parent) '.env'
$loaded = @()

function Test-Usable([string]$Value) {
  return [bool]$Value -and -not $Value.StartsWith('encrypted:')
}

function Read-LibraryKey([string]$Key, [string]$File) {
  $path = Join-Path $library $File
  if (-not (Test-Path $path)) { throw "The env library file $File is missing." }
  for ($try = 1; $try -le 4; $try++) {
    $output = & dotenvx get $Key -f $path --overload 2>$null
    $code = $LASTEXITCODE
    $value = (($output | Out-String) + '').Trim()
    if ($code -eq 0 -and (Test-Usable $value)) { return $value }
    Write-Host "  ${Key}: try $try of 4 gave no usable value"
    Start-Sleep -Seconds 2
  }
  throw "${Key} could not be read from $File (empty or still encrypted after four tries)."
}

function Read-RepoKey([string]$Key) {
  if (-not (Test-Path $repoEnv)) { throw 'The repo .env is missing.' }
  $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*(.*)$'
  $value = $null
  foreach ($line in Get-Content -LiteralPath $repoEnv) {
    if ($line -match $pattern) { $value = $Matches[1].Trim().Trim('"').Trim("'") }
  }
  if (-not (Test-Usable $value)) { throw "${Key} is missing from the repo .env." }
  return $value
}

function Set-Loaded([string]$Name, [string]$Value) {
  Set-Item -Path "Env:$Name" -Value $Value
  $script:loaded += $Name
}

if (-not $Status -and -not $Stop -and -not $PublicBaseUrl) {
  throw 'Give -PublicBaseUrl (the public address of the app), or use -Status or -Stop.'
}

try {
  Write-Host 'Loading keys from the env library (about ten seconds each)...'
  Set-Loaded WHATSAPP_ACCESS_TOKEN (Read-LibraryKey WHATSAPP_ACCESS_TOKEN 'lajio.env')
  Write-Host '  WHATSAPP_ACCESS_TOKEN: loaded'
  $pyArgs = @()
  if ($Status) {
    $pyArgs += '--status'
  } else {
    Set-Loaded WHATSAPP_APP_SECRET (Read-LibraryKey WHATSAPP_APP_SECRET 'lajio.env')
    Write-Host '  WHATSAPP_APP_SECRET: loaded'
    if ($PublicBaseUrl) {
      Set-Loaded PUBLIC_BASE_URL $PublicBaseUrl
      Set-Loaded HACK_BRIDGE_KEY (Read-RepoKey HACK_BRIDGE_KEY)
      Write-Host '  HACK_BRIDGE_KEY: loaded'
    }
    if ($Stop) {
      $pyArgs += '--stop'
    } else {
      Set-Loaded LUMI_ALLOWED (Read-LibraryKey LUMI_ALLOWED 'lumicode-agents.env')
      Write-Host '  LUMI_ALLOWED: loaded'
      $pyArgs += @('--minutes', "$Minutes")
    }
  }
  & uv run --project $PSScriptRoot python (Join-Path $PSScriptRoot 'main.py') @pyArgs
  $exit = $LASTEXITCODE
} finally {
  foreach ($name in $loaded) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
}
exit $exit
