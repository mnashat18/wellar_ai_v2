# Local container instructions

This Compose configuration is for local development only. API and worker share one local Docker volume and SQLite file. It is not a multi-host or production deployment architecture.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Replace the synthetic development token for any shared staging test; never place production credentials in this file. No external Directus, Redis, or PostgreSQL service is assumed or configured.

## Independently scaled scan workers

`deployment/docker-compose.scalable.yml` runs one isolated worker container per replica. Set `WELLAR_WORKER_COUNT` to the desired replica count; set per-worker CPU and memory limits with `WELLAR_WORKER_CPU_LIMIT` and `WELLAR_WORKER_MEMORY_LIMIT`. The worker count must be greater than zero, and counts above one require PostgreSQL. SQLite is supported only for single-host development.

Each worker initializes the bounded media decoder warm-up before creating its PID-specific readiness marker. Compose worker health checks require that marker. A startup failure exits nonzero and is visible to the container restart policy. Worker IDs are generated independently at process startup. The API readiness endpoint reports API dependencies; it does not assert that a worker replica is ready.

For isolated Sprint 08 validation, `WELLAR_SPRINT08_WORKER_COUNT` controls disposable worker replicas. The test stack remains PostgreSQL/MinIO only and must not be used with shared or production resources. No worker scaling measurements are implied by this configuration documentation.
