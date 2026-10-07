# Decisions

Short records of choices that are not obvious from the code.

## 2026-10-07: Keep the live widget's `church_id` contract
The website embed carries `data-church-id="2"`. Rather than change the live site, the public
endpoints still accept `church_id`, compare it against the organization's own id and its
stored `legacy_widget_id`, and return the same errors as before for anything else. The id is
also optional now. Revisit if the embed is ever rewritten.

## 2026-10-07: The organization keeps its source row's identity
The migration builds the one `organization` row from church 2 and records 2 as
`legacy_widget_id`. Uploaded documents stay under `uploads/2/` (`documents.UPLOAD_SUBDIR`)
instead of being moved on a production volume.

## 2026-10-07: A duplicate sign-up is folded in, not archived
"Dalton First Methodist church" (church 6) was a second sign-up by a staff member. Its user
and one communications request were moved into the organization; the user became `staff`.

## 2026-10-07: Other tenants are archived to a file, then deleted by the migration
Their rows were exported (checksummed) before the migration runs. The migration refuses to
delete them unless `WESLEY_CONFIRM_DROP_TENANTS=yes` is set, so the destructive step cannot
happen by accident on a deploy.

## 2026-10-07: The migration is PostgreSQL-only and irreversible
Production is PostgreSQL; local development builds its schema from the models. There is no
`downgrade()`. The rollback is the tested restore in `docs/RUNBOOK.md`.

## 2026-10-07: Theology is one fixed profile
No selector, no other denominations. The United Methodist profile and the authority order
(local practice and approved Q&A outrank the profile) are unchanged, so existing answers do
not move. Compared on identical data before and after the conversion: same retrieved context
for all 30 test questions.

## 2026-10-07: Accounts are limited to the church email domain
`ORG_DOMAIN` (default `daltonfumc.com`) is checked on login, invitation and acceptance. This
is an interim control; Phase 1b verifies the domain from a Google token server-side.

## 2026-10-07: The sender address stays `wesleyai.co` for now
Resend is configured for that domain. Change `FROM_EMAIL` once a church domain is verified.
