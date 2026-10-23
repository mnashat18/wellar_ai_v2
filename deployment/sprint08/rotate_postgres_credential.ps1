param([switch]$Recover)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$envPath = Join-Path $projectRoot '.env.sprint08'
$composeFile = 'deployment/sprint08/compose.yaml'
$projectName = 'wellar-v2-sprint08'
$role = 'wellar_sprint08'
$database = 'wellar_v2_sprint08_test'

function Stop-Safely([string]$Message) {
    Write-Error $Message -ErrorAction Continue
    exit 1
}

function Test-ProtectedEnvironment([string]$Path) {
    $identity = (whoami).Trim()
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.Access)
    $expected = @($identity, 'NT AUTHORITY\SYSTEM')
    $actual = @($rules | ForEach-Object { $_.IdentityReference.Value })
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2 -or
        (@($actual | Sort-Object) -join '|') -ne (@($expected | Sort-Object) -join '|')) {
        return $false
    }
    foreach ($rule in $rules) {
        if ($rule.IsInherited -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
            return $false
        }
    }
    return $true
}

function Get-EnvValues([string]$Text) {
    $values = @{}
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $Matches[1]
            if ($values.ContainsKey($key)) { throw 'duplicate environment key' }
            $values[$key] = $Matches[2].Trim().Trim("'").Trim('"')
        }
    }
    return $values
}

function Invoke-Docker([string]$Arguments, [string]$StandardInput = '') {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = 'docker'
    $start.Arguments = $Arguments
    $start.WorkingDirectory = $projectRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = ($StandardInput.Length -gt 0)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    if (-not $process.Start()) { throw 'docker process did not start' }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if ($StandardInput.Length -gt 0) {
        $process.StandardInput.Write($StandardInput)
        $process.StandardInput.Close()
    }
    if (-not $process.WaitForExit(30000)) {
        try { $process.Kill() } catch {}
        throw 'docker command timed out'
    }
    $outText = $stdout.GetAwaiter().GetResult()
    $null = $stderr.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) { throw 'docker command failed' }
    return $outText.Trim()
}

function Write-ExistingFile([string]$Path, [byte[]]$Bytes) {
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None)
    try {
        if ($stream.Length -ne $Bytes.Length) { throw 'credential file length changed' }
        $stream.Position = 0
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}

function Test-ByteEquality([byte[]]$Left, [byte[]]$Right) {
    if ($Left.Length -ne $Right.Length) { return $false }
    for ($i = 0; $i -lt $Left.Length; $i++) {
        if ($Left[$i] -ne $Right[$i]) { return $false }
    }
    return $true
}

if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    Stop-Safely 'The existing protected .env.sprint08 file is required; no rotation was attempted.'
}
if (-not (Test-ProtectedEnvironment $envPath)) {
    Stop-Safely 'Credential-file ACL is not restricted to the current user and SYSTEM; no rotation was attempted.'
}

$originalBytes = [System.IO.File]::ReadAllBytes($envPath)
$originalText = [System.Text.Encoding]::UTF8.GetString($originalBytes)
try { $envValues = Get-EnvValues $originalText }
catch { Stop-Safely 'Credential file structure is invalid or contains duplicate keys; no rotation was attempted.' }
$requiredKeys = @('WELLAR_SPRINT08_POSTGRES_USER','WELLAR_SPRINT08_POSTGRES_PASSWORD','WELLAR_POSTGRES_DSN')
if (@($requiredKeys | Where-Object { -not $envValues.ContainsKey($_) }).Count -gt 0) {
    Stop-Safely 'Required Sprint 08 PostgreSQL settings are missing; no rotation was attempted.'
}

if ($envValues['WELLAR_SPRINT08_POSTGRES_USER'] -cne $role) {
    Stop-Safely 'The configured PostgreSQL role is not the dedicated Sprint 08 test role; no rotation was attempted.'
}
$oldPassword = $envValues['WELLAR_SPRINT08_POSTGRES_PASSWORD']
$oldDsn = $envValues['WELLAR_POSTGRES_DSN']
$passwordWellFormed = $oldPassword -match '^[A-Fa-f0-9]{48}$'
$expectedOldDsn = if ($passwordWellFormed) { '^postgresql://' + $role + ':' + [regex]::Escape($oldPassword) + '@postgres:5432/' + $database + '$' } else { '^postgresql://' + $role + ':[A-Fa-f0-9]{0,48}@postgres:5432/' + $database + '$' }
$stateConsistent = $oldDsn -match $expectedOldDsn
if (-not $stateConsistent) {
    if (-not $Recover -or $oldDsn -notmatch ('^postgresql://' + $role + ':[^@]*@postgres:5432/' + $database + '$')) {
        Stop-Safely 'Credential file state is inconsistent. Review the recovery procedure; no rotation was attempted.'
    }
    $oldPassword = $null
    Write-Host 'Recovery mode: the database role and file may be out of sync. The verified local database socket will be used.'
} elseif (-not $passwordWellFormed) {
    Stop-Safely 'Stored PostgreSQL password format is invalid; use -Recover only after verifying the isolated Compose target.'
}

$composePrefix = "compose --project-name $projectName --env-file .env.sprint08 --file $composeFile"
try {
    $containerIdText = Invoke-Docker "$composePrefix ps -q postgres"
    $containerIds = @($containerIdText -split "`r?`n" | Where-Object { $_ })
    if ($containerIds.Count -ne 1 -or $containerIds[0] -notmatch '^[a-fA-F0-9]{12,64}$') {
        Stop-Safely 'Expected exactly one PostgreSQL container in the isolated Sprint 08 Compose project.'
    }
    $labels = Invoke-Docker "inspect --format '{{.Config.Labels}}' $($containerIds[0])"
    if ($labels -notmatch 'com\.docker\.compose\.project:wellar-v2-sprint08' -or
        $labels -notmatch 'com\.docker\.compose\.service:postgres') {
        Stop-Safely 'The discovered PostgreSQL container is not labeled as the Sprint 08 project service.'
    }
    $identity = Invoke-Docker "$composePrefix exec -T postgres psql -U $role -d $database --no-psqlrc -Atqc `"SELECT current_user || '|' || current_database()`""
    if ($identity -cne "$role|$database") {
        Stop-Safely 'PostgreSQL identity preflight did not match the dedicated test role and database.'
    }
}
catch {
    Stop-Safely 'Docker/PostgreSQL preflight failed. No credential change was attempted; command details were suppressed.'
}

Write-Host 'Target verified: wellar-v2-sprint08 / postgres / wellar_sprint08 / wellar_v2_sprint08_test.'
Write-Host 'No database, bucket, container, or volume will be recreated or deleted.'
$confirmation = Read-Host 'Type ROTATE SPRINT08 TEST ROLE to proceed'
if ($confirmation -cne 'ROTATE SPRINT08 TEST ROLE') {
    Write-Host 'Cancelled. No credential change was made.'
    exit 0
}

$randomBytes = New-Object byte[] 24
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$newPassword = $null
$newBytes = $null
$sql = $null
$rollbackSql = $null
$newDsn = $null
$newText = $null
$dbChanged = $false
try {
    $rng.GetBytes($randomBytes)
    $newPassword = [System.BitConverter]::ToString($randomBytes).Replace('-', '').ToLowerInvariant()
    $sql = "ALTER ROLE $role WITH PASSWORD '$newPassword';`n"
    $null = Invoke-Docker "$composePrefix exec -T postgres psql -U $role -d $database --no-psqlrc --set ON_ERROR_STOP=1" $sql
    $dbChanged = $true

    $newDsn = "postgresql://$role`:$newPassword@postgres:5432/$database"
    $newText = [regex]::Replace($originalText, '(?m)^WELLAR_SPRINT08_POSTGRES_PASSWORD=.*$', "WELLAR_SPRINT08_POSTGRES_PASSWORD=$newPassword")
    $newText = [regex]::Replace($newText, '(?m)^WELLAR_POSTGRES_DSN=.*$', "WELLAR_POSTGRES_DSN=$newDsn")
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newText)

    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $currentHash = $hasher.ComputeHash([System.IO.File]::ReadAllBytes($envPath))
        $originalHash = $hasher.ComputeHash($originalBytes)
    }
    finally { $hasher.Dispose() }
    if (-not (Test-ByteEquality $currentHash $originalHash)) {
        throw 'credential file changed during rotation'
    }
    Write-ExistingFile $envPath $newBytes
    if (-not (Test-ProtectedEnvironment $envPath)) { throw 'credential file ACL changed during rotation' }
    Write-Host 'Rotation completed. Secret values were not printed or written to a temporary file.'
    Write-Host 'Run deployment/sprint08/run_integration_tests.py from the V2 root to verify live PostgreSQL and MinIO.'
}
catch {
    $fileRestored = $false
    try { Write-ExistingFile $envPath $originalBytes; $fileRestored = $true } catch {}
    $dbRestored = $false
    if ($dbChanged -and $null -ne $oldPassword) {
        try {
            $rollbackSql = "ALTER ROLE $role WITH PASSWORD '$oldPassword';`n"
            $null = Invoke-Docker "$composePrefix exec -T postgres psql -U $role -d $database --no-psqlrc --set ON_ERROR_STOP=1" $rollbackSql
            $dbRestored = $true
        }
        catch {}
    }
    if ($dbChanged -and (-not $dbRestored -or -not $fileRestored)) {
        Stop-Safely 'Rotation did not finish cleanly. No secret was printed. Use the recovery procedure in V2_LOCAL_CREDENTIAL_ROTATION_REPORT.md before any service restart.'
    }
    Stop-Safely 'Rotation failed and the prior state was restored where possible. No secret or subprocess error was printed.'
}
finally {
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    if ($null -ne $newBytes) { [Array]::Clear($newBytes, 0, $newBytes.Length) }
    $rng.Dispose()
    $newPassword = $null
    $sql = $null
    $rollbackSql = $null
    $newDsn = $null
    $newText = $null
}
