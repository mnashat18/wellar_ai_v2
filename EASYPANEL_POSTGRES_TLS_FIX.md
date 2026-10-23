# Enable verified TLS for the isolated Wellar V2 PostgreSQL service

**Scope:** Wellar V2 staging PostgreSQL only. This runbook does not change a database, mount, service, or host. Apply it in the EasyPanel project that owns the V2 PostgreSQL service. Do not use it against Directus, Legacy, or production.

## Findings from the V2 configuration

- V2 reads its connection string from `WELLAR_POSTGRES_DSN`; do not print the value when inspecting it.
- Staging/production configuration requires `sslmode=verify-full` (`app/core/config.py`, staging security validation). The 5-second pool initialization timeout is a symptom when the pool cannot establish its initial connection; increasing it does not fix TLS incompatibility.
- The V2 staging Compose example uses a private network (default `wellar-v2-staging`, configurable by `WELLAR_STAGING_NETWORK`). Use the actual EasyPanel network shared by the API, worker, and this PostgreSQL service.
- The existing separate database is PostgreSQL 17, database `wellar_v2`, user `wellar_v2_user`, and the stated DSN hostname is `continuity_wellar-v2-postgres`. The server currently reports SSL off, according to the owner-provided observation; it has not been checked from this workstation.

## TLS design

Use a private CA dedicated to the isolated V2 staging service (or EasyPanel's supported private-CA facility). Issue a PostgreSQL server certificate with **SAN `DNS:continuity_wellar-v2-postgres`**. The hostname in the DSN must remain exactly that DNS name. If the actual EasyPanel network alias differs, use that actual alias consistently in the DSN and certificate SAN; do not connect by IP with `verify-full`.

Keep the CA private key offline or in the owner's approved secret manager. The PostgreSQL server private key and CA certificate are secrets/configuration assets, not source files. Do not put any key, certificate bundle, or credential in GitHub, the V2 repository, a Docker image, or an ordinary report. The CA certificate is public material cryptographically, but distribute it through the same controlled deployment channel to prevent accidental substitution.

### Create the CA and server certificate (owner workstation)

Run these commands on a trusted administrative machine with OpenSSL. The example creates files under a private working directory; choose a protected location outside the repository and remove the temporary CSR/config after secure delivery. OpenSSL will prompt for the CA key passphrase. Do not put passphrases in command arguments or shell history.

```sh
umask 077
mkdir wellar-v2-pg-tls
cd wellar-v2-pg-tls
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -aes-256-cbc -out v2-staging-ca.key
openssl req -x509 -new -sha256 -days 1825 -key v2-staging-ca.key \
  -out v2-staging-ca.crt -subj "/CN=Wellar V2 Staging PostgreSQL CA"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out server.key
openssl req -new -key server.key -out server.csr \
  -subj "/CN=continuity_wellar-v2-postgres"
cat > server.ext <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:continuity_wellar-v2-postgres
EOF
openssl x509 -req -in server.csr -CA v2-staging-ca.crt -CAkey v2-staging-ca.key \
  -CAcreateserial -out server.crt -days 397 -sha256 -extfile server.ext
openssl verify -CAfile v2-staging-ca.crt server.crt
openssl x509 -in server.crt -noout -checkhost continuity_wellar-v2-postgres
```

The last two commands must report successful verification and hostname match before installation. Keep `v2-staging-ca.key` offline; it is not needed by PostgreSQL at runtime. Plan certificate renewal before expiry and repeat the checks before replacing the server certificate.

## EasyPanel PostgreSQL configuration

### Preserve the current PG17 database

1. In EasyPanel, identify the **existing V2 PostgreSQL service**, its exact PG17 image/tag, its persistent data volume name and in-container data path, and the private Docker network. Record these values. Confirm the service is not Directus/Legacy.
2. Take and verify an owner-approved backup of the existing V2 database before changing service configuration. This is a configuration change; do not initialize a new cluster.
3. Do **not** remove, rename, detach, reinitialize, or recreate the persistent PostgreSQL data volume. Do not run `docker compose down -v`, a reset-storage action, or a major-version image upgrade. Keep the PostgreSQL 17 image and its existing data mount unchanged.
4. Add a separate persistent secret/file mount for TLS assets, outside the database data volume. Example container paths:
   - server certificate: `/run/postgres-tls/server.crt`
   - server private key: `/run/postgres-tls/server.key`
   - CA certificate for client distribution: `/run/postgres-tls/v2-staging-ca.crt`

   Mount the certificate and key read-only. Use EasyPanel secret/file mounts or an owner-controlled persistent configuration volume. Do not store the TLS assets beneath the PG data directory.
5. Set PostgreSQL's effective configuration (through the service's supported config/command mechanism):

```conf
ssl = on
ssl_cert_file = '/run/postgres-tls/server.crt'
ssl_key_file = '/run/postgres-tls/server.key'
```

Keep the existing `port`, `listen_addresses`, data directory, and PG17 image. PostgreSQL only needs a server certificate and key to serve TLS. `ssl_ca_file` is not needed unless the service intentionally requires client certificates (mutual TLS); V2's server-certificate verification uses the CA file on each V2 client instead.

6. Check `pg_hba.conf` for the V2 database/user and application network. Permit the V2 app using `hostssl` with the existing least-privilege authentication rule (normally `scram-sha-256`). Remove or narrow any matching plaintext `host` rule for that source/user/database so server-side policy also rejects plaintext. Preserve unrelated access rules. Example shape only; replace the CIDR with the actual private V2 network:

```conf
hostssl  wellar_v2  wellar_v2_user  <V2_PRIVATE_CIDR>  scram-sha-256
```

Do not broaden access to `0.0.0.0/0` and do not expose port 5432 publicly for this change.

### File ownership and permissions

The PostgreSQL process must own the private key and the key must be mode `0600`; the server certificate and CA certificate may be `0644`. The containing directory must allow traversal by the PostgreSQL process and should be `0750` or stricter. Determine the **actual UID/GID** in the exact EasyPanel image (`id postgres` inside that image); do not assume an image's UID. Set ownership at the source of the persistent mount or secret definition before attaching it. A read-only bind mount cannot be repaired with `chown` from inside the container. If EasyPanel secret mounts cannot provide compatible ownership/modes, use a protected persistent config volume or a narrowly scoped init/copy mechanism that writes the key with `0600`, then mounts/uses that copy; never make the private key world-readable to work around permissions.

Before restarting, inspect the effective config and confirm PostgreSQL can read the files as its runtime user. A bad mode or owner should fail closed and be corrected; do not loosen permissions beyond the minimum.

## Configure both V2 clients

Distribute only `v2-staging-ca.crt` to the V2 API and worker through EasyPanel's protected file/secret mount, read-only at the same path in both containers, for example:

```text
/run/wellar-tls/v2-staging-ca.crt
```

Set `WELLAR_POSTGRES_DSN` as an EasyPanel secret/environment value using the existing password, URL-encoded as required by a URI DSN:

```text
postgresql://wellar_v2_user:<URL-ENCODED-EXISTING-PASSWORD>@continuity_wellar-v2-postgres:5432/wellar_v2?sslmode=verify-full&sslrootcert=/run/wellar-tls/v2-staging-ca.crt&connect_timeout=5
```

Preserve any other existing DSN query settings. Never put the actual DSN in a terminal command, deployment report, source file, or log. Set the same DSN and CA mount for API and worker. Do not replace `verify-full` with `require`, `prefer`, or `disable`.

## Apply and restart sequence

1. Confirm the backup and record the existing PostgreSQL data volume identity/mount. Stage the TLS files and configuration without changing that volume.
2. Apply the PostgreSQL TLS settings and `hostssl` rule. Use EasyPanel's controlled **restart/redeploy of the existing PostgreSQL service** so the container receives the mounts and settings. If the UI only applies new mounts during container recreation, recreate the service container while explicitly retaining the same existing PG17 persistent data volume and mount path. Do not use any action that deletes volumes or initializes storage. Schedule a brief V2 staging interruption.
3. Confirm PostgreSQL is healthy, `SHOW ssl` is `on`, and logs show no certificate/key permission or parse errors. A configuration reload can load changed TLS configuration, but a service restart is required when adding/changing container mounts; use the controlled restart here. Existing database sessions do not change TLS state, so reconnect clients for verification.
4. Update the CA mount and `WELLAR_POSTGRES_DSN` on both V2 API and worker, then restart/redeploy both containers so they receive the CA file and DSN. Verify API and worker health. If the pool still times out, inspect sanitized connection/server errors and confirm network DNS, mounts, key permissions, certificate chain, and SAN; do not increase the timeout as a substitute for fixing TLS.

## Read-only verification from the V2 Docker network

Run an ephemeral diagnostic container attached to the **same private Docker network** as the V2 PostgreSQL service. EasyPanel may use a project-specific network name; obtain the exact name from the V2 service's network settings/host `docker network ls` and confirm the API/worker and PostgreSQL are attached. Do not attach to the Legacy or Directus network, publish ports, or use a production host. Mount the CA certificate read-only into the diagnostic container. The following is a host-side example; substitute only the verified V2 network and protected CA file path:

```sh
docker run --rm -it --network <EXACT_V2_PRIVATE_NETWORK> \
  --mount type=bind,src=<PROTECTED_PATH_TO_CA_CERT>,dst=/run/wellar-tls/ca.crt,readonly \
  postgres:17 psql \
  "host=continuity_wellar-v2-postgres port=5432 dbname=wellar_v2 user=wellar_v2_user sslmode=verify-full sslrootcert=/run/wellar-tls/ca.crt connect_timeout=5"
```

`psql` prompts interactively for the existing database password. Do not put it in the URI/command, set it in a printed environment variable, or capture it in shell history. Confirm the connected database/user and negotiated TLS:

```sql
SELECT current_database(), current_user;
SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid();
SHOW ssl;
```

Expected: database `wellar_v2`, user `wellar_v2_user`, `pg_stat_ssl.ssl = true`, and `SHOW ssl = on`.

### Prove hostname mismatch is rejected

From the same diagnostic container, first resolve the correct PostgreSQL hostname to its private IP (for example `getent ahostsv4 continuity_wellar-v2-postgres`). Then run `psql` with `host=wrong-name.invalid` and `hostaddr=<RESOLVED_PRIVATE_IP>`, keeping the same CA, database, user, and `sslmode=verify-full`:

```sh
psql "host=wrong-name.invalid hostaddr=<RESOLVED_PRIVATE_IP> port=5432 dbname=wellar_v2 user=wellar_v2_user sslmode=verify-full sslrootcert=/run/wellar-tls/ca.crt connect_timeout=5"
```

Enter the password at the prompt if requested. This command must fail with a certificate hostname verification error. `hostaddr` directs the TCP connection to the resolved private server while `host` remains the name checked against the certificate. If it fails due to DNS/connectivity instead, the mismatch test is **inconclusive**; do not count that as a pass. Do not change the server certificate to perform this negative test.

After diagnostics, remove the ephemeral container (the `--rm` option does so). Do not leave an exposed port or permanent diagnostic service.

## Rollback

If PostgreSQL does not start or TLS verification fails:

1. Keep the database volume untouched. Use EasyPanel to stop the V2 API/worker if they are repeatedly failing to connect.
2. Restore the recorded PostgreSQL TLS configuration and mount definitions or correct the certificate/permissions, then restart the same PostgreSQL 17 service with the same data volume. Inspect logs without printing credentials.
3. Restore the recorded V2 API/worker environment and CA mounts only to a known-good configuration that still enforces `sslmode=verify-full`. If no valid TLS endpoint is available, leave V2 API/worker stopped until the certificate/server configuration is corrected. Do not roll back by disabling client TLS validation.
4. Verify the existing database remains present and the TLS connection succeeds before resuming V2 traffic.

No command in this runbook drops, recreates, or migrates the PostgreSQL data volume. Do not use a volume reset as a rollback step.

## Verification status for this workstation run

- **Configuration assessed:** V2 requires `sslmode=verify-full`; existing data and service configuration were not changed.
- **Live TLS verification:** Not run. This workstation has not connected to or modified the EasyPanel server.
- **Owner action:** Apply the steps to the isolated V2 PostgreSQL service, then run both diagnostic checks from its private network and confirm the positive query succeeds and the hostname-mismatch check fails specifically for certificate identity mismatch.
