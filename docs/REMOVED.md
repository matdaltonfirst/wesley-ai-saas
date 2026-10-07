# What was removed, and why

The app now serves one church, so everything that existed to serve many was removed rather
than hidden. The last multi-tenant version is the git tag `pre-single-org`.

| Removed | Why |
|---|---|
| Stripe billing, trials, manual payment tracking, billing admin, the subscribe and success pages, six billing emails, the trial and billing jobs | One organization, nothing to bill. Also removed `Church.is_active`, which returned true for a missing trial date. |
| Public signup and the onboarding wizard | There are no new tenants. Signup was an open door for anyone to spend AI budget. Accounts now come only from invitation, on the church email domain. |
| The `churches` table and every `church_id` column and index | Replaced by one `organization` row. Per-church uniqueness became organization-wide uniqueness. |
| Super-admin panel (church list, per-church token counts, billing exempt toggle, password reset that returned a plaintext temporary password) | Platform operator tooling for a platform that no longer exists. `/admin` now only edits the public chatbot's instructions and shows usage, for admins. |
| `SUPER_ADMIN_EMAIL`, `BILLING_EXEMPT_DOMAINS`, `STRIPE_*` settings | No longer read. The `ORG_*` settings replace them. |
| The denomination selector and the Southern Baptist, Global Methodist, non-denominational and custom profiles, the isolation matrix and the foreign-terms detector | This is a United Methodist church. The theology is fixed. The United Methodist content was kept unchanged. |
| `umc_facts.py` compatibility shim | Everything imports the profile directly. |
| `migrate_to_postgres.py` and its tests | The SQLite to Postgres copy ran once, on 4 September 2026. |
| `docs/denominational-architecture.md`, `docs/scaling-readiness.md` | Described the removed multi-denomination design and the pre-Postgres setup. The Postgres runbook is kept as history. |
| Log line that printed the first 8 characters of the Gemini key | A secret does not belong in logs. |

Other tenants' data was archived, not lost: see the archive in `docs/RUNBOOK.md`.

Not removed on purpose: password login and staff invitations. They stay until Google
Workspace sign-in replaces them in Phase 1b.

## Phase 1b removals

| Removed | Why |
|---|---|
| Staff invitations (`invites` table, invitation email, accept page, `/api/staff/*`) | Admins add people on the People screen and people sign in with Google. |
| `users.role` (`admin` or `staff`) | Replaced by `user_roles` and the permission matrix. |
| `routes/widget.py` | Split into `routes/public_api.py` (the public chatbot) and `routes/staff_widget.py` (staff views of chat logs, guests, Q&A and snippets). |
| The `widget=True` branch of `helpers.build_system_prompt` and the shared loaders on the public path | The public path has its own prompt builder and its own queries in `public_knowledge.py`. |
| The in-memory public rate limiter | Replaced by shared counters in the database. |
| `Access-Control-Allow-Origin: *` on public data endpoints | Replaced by the origin check. The widget script itself stays embeddable. |
