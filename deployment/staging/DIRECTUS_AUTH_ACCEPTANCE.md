# Isolated staging: Directus authentication

This is a preparation template, not a deployable or approved environment. No provider URL, account, secret, schema or service is included. Store populated values in the staging secret manager and keep them outside source control.

## Current compose limitation

`deployment/docker-compose.scalable.yml` hardcodes `WELLAR_AUTH_MODE=oidc` and requires issuer, audience and JWKS variables while building the Compose model. Setting `WELLAR_AUTH_MODE=directus` in an env file alone therefore does not switch this stack safely. Do not use dummy OIDC values to work around that requirement. A separately reviewed staging-only Compose override or an explicitly approved change to the scalable manifest is required before deployment.

## Directus user-token permissions

The backend forwards the employee's bearer token to two REST reads on every authenticated V2 request:

1. `GET /users/me?fields=id,status` — the authenticated user must be allowed to read their own ID and status; the returned ID is the subject and status must be `active`.
2. `GET /items/business_profile_members?fields=id,user,business_profile,status&filter[user][_eq]=<verified-user-id>&filter[status][_eq]=active&limit=2` — the role must have read access to this collection and these four fields. The result must expose membership rows for the current user and active status, with the related `business_profile` reference. Return at most two rows so ambiguity is detected. Configure an item permission bound to the authenticated user (for example `user = $CURRENT_USER`) and status `active`; API filters are not a substitute for role-level item permissions.

The caller's token, not an administrative/service token, is used for both reads. Do not grant create/update/delete or broad tenant access for this verification role. The separate optional Directus pull integration uses a service credential and is disabled by default; it is not needed for identity validation.

Directus permission rules are configured on policies and can constrain both item filters and readable fields. See [Directus permissions](https://docs.directus.io/reference/system/permissions) and [filter rules](https://docs.directus.io/reference/filter-rules). The exact role/policy and collection field mapping must be checked against the isolated instance's schema before acceptance.

## Tenant selection policy

The Flutter app supports multiple active organization memberships, has a workspace picker, and persists the last selected membership ID locally. That local selection is UI state only and cannot authorize a tenant. Current V2 Directus auth intentionally denies zero or multiple active memberships.

Before supporting multi-organization accounts, the product owner must choose whether an employee may use multiple organizations in V2. If yes, the client may send a selected membership identifier (or tenant selector), but the backend must resolve it under the caller's Directus token, verify that membership belongs to `/users/me` and is active, and derive the tenant from the returned `business_profile`. A stale, revoked, missing or ambiguous selection must fail closed. Never use a saved/default tenant without server verification. This contract is not implemented.

## Acceptance sequence

- Provision a separate HTTPS Directus staging instance and V2 staging database/bucket; verify certificates and private network paths.
- Apply a least-privilege Directus policy to synthetic test accounts: one active membership, no membership, inactive/revoked user, and two active memberships.
- After approval, add a staging-only Compose wiring change, render configuration without secrets, deploy only isolated staging, and run the existing synthetic identity/API tests plus HTTP checks for invalid token and provider outage.
- Verify refresh/logout against the same Directus staging issuer using synthetic accounts; do not include tokens in logs or reports.
- Keep no authorization cache initially: current behavior observes revocation/membership changes on each request. Benchmark request volume in bounded staging later. Any positive cache requires an explicit maximum revocation delay and invalidation design.

## Required operator inputs and approvals

- Confirmation that Directus remains the V2 identity authority for the bridge period.
- Decision on whether multi-organization employees are supported and, if so, the selected-membership UX/policy.
- Isolated staging Directus HTTPS URL, schema/role owner, test accounts and scoped read policy.
- Isolated staging database, private object bucket, secret-manager entries, resource limits and network allow-list.
- Explicit approval for staging-only Compose changes and service startup before any live staging integration test.
