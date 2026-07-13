# Scaling Readiness Notes

As this repo scales, the main things that need attention are infrastructure and architecture more than feature correctness. The biggest concern is the current SQLite-based production setup, which is fine for early-stage usage but will likely become a bottleneck with more tenants, concurrent writes, background processing, and multi-instance deployment. Moving to PostgreSQL would be one of the highest-value scaling improvements.

Another important issue is the use of raw background threads inside request handlers for tasks like guest sync and email sending. That approach works for lightweight workloads, but it becomes unreliable at scale because jobs can be lost on restart, failures are hard to retry, and observability is limited. A proper background job system with retries and monitoring would make this much safer.

The app also handles schema evolution with startup-time migration logic inside `app.py`, including inline `ALTER TABLE` statements. That is manageable early on, but it becomes risky as deployments and schema changes increase. A standard migration workflow would make database changes safer and easier to manage.

From an application-performance perspective, the retrieval and scoring flow may also become expensive as more tenant content is added. The current design appears to assemble and score documents, sermons, calendar content, and other sources at request time. That can increase latency as usage and data volume grow, so caching, precomputation, and stronger indexing/search infrastructure would help significantly.

Finally, the codebase has some maintainability risks that will affect team scaling. Large mixed-responsibility files like `app.py`, `routes/widget.py`, large templates, and big frontend scripts are manageable for a small team, but they will slow development over time and make refactoring harder. Breaking these into smaller modules and introducing clearer service boundaries would improve long-term maintainability.

## Recommended priorities

1. Move from SQLite to PostgreSQL.
2. Replace raw background threads with a proper job queue.
3. Adopt a real migration system.
4. Improve retrieval, caching, and indexing strategy.
5. Refactor the largest files into smaller, more modular components.

## Scaling checklist

- [ ] Migrate production database from SQLite to PostgreSQL.
- [ ] Add a versioned migration system such as Alembic/Flask-Migrate.
- [ ] Remove startup-time schema mutation logic from `app.py`.
- [ ] Replace request-spawned background threads with a proper job queue.
- [ ] Add retry, timeout, and failure monitoring for async jobs.
- [ ] Separate web, worker, and scheduled job responsibilities.
- [ ] Cache or precompute retrieval inputs for documents, sermons, and calendar data.
- [ ] Add stronger indexing/search infrastructure for tenant knowledge retrieval.
- [ ] Measure and monitor chat/widget latency under larger tenant datasets.
- [ ] Break up large files such as `app.py`, `routes/widget.py`, and large frontend assets.
- [ ] Move business logic out of route handlers into service modules.
- [ ] Add CI to run tests automatically on pushes and pull requests.
- [ ] Add linting/formatting checks such as `ruff` and `black`.
- [ ] Add structured logging and basic observability for errors and performance.
- [ ] Review multi-tenant isolation and performance characteristics before larger growth.
