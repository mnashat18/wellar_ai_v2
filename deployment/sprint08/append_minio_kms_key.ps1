$ErrorActionPreference = 'Stop'

$envPath = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path '.env.sprint08'
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw 'The existing .env.sprint08 file is required; no credentials were generated.'
}

$identity = (whoami).Trim()
$acl = Get-Acl -LiteralPath $envPath
$rules = @($acl.Access)
$expectedIdentities = @($identity, 'NT AUTHORITY\SYSTEM')
$actualIdentities = @($rules | ForEach-Object { $_.IdentityReference.Value })
$aclIsRestricted = $acl.AreAccessRulesProtected -and $rules.Count -eq 2
foreach ($rule in $rules) {
    if ($rule.IsInherited -or
        $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
        $aclIsRestricted = $false
    }
}
if (-not $aclIsRestricted -or
    (@($actualIdentities | Sort-Object) -join '|') -ne (@($expectedIdentities | Sort-Object) -join '|')) {
    throw 'Credential-file ACL is not restricted to the current user and SYSTEM; no key was written.'
}

$existingKey = Select-String -LiteralPath $envPath -Pattern '^\s*WELLAR_SPRINT08_MINIO_KMS_SECRET_KEY\s*=' -Quiet
if ($existingKey) {
    throw 'The Sprint 08 MinIO KMS key already exists; refusing to replace or duplicate it.'
}

$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($bytes)
    $value = 'minio-sprint08:' + [Convert]::ToBase64String($bytes)
    $line = 'WELLAR_SPRINT08_MINIO_KMS_SECRET_KEY=' + $value
    Add-Content -LiteralPath $envPath -Value $line -Encoding ascii
}
finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
    $rng.Dispose()
    $value = $null
    $line = $null
}

Write-Output 'Added one test-only MinIO KMS key; the value was not printed and existing credentials were preserved.'
