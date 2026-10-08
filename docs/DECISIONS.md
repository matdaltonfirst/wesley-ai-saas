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

## 2026-10-07: Roles are a table; the matrix is code with admin overrides
Six roles, named permissions, defaults in `permissions.py`, overrides in `role_permissions`.
Admin gets everything except giving. Sensitive domains (pastoral notes, background checks,
children's names, giving by donor) are deliberately not permissions, so they cannot be granted
by mistake or by an admin screen. A test enforces that.

## 2026-10-07: Admins cannot lock themselves out
`team.manage` and `audit.read` are always on for Admin, the last active admin cannot be
demoted or deactivated, and nobody can deactivate themselves.

## 2026-10-07: People are added by an admin, then sign in with Google
Being on the church Google domain is not enough. This keeps a former volunteer or a
stranger with a church address out, and gives every new person an explicit role (default
deny). Invitations and the invitation email were removed.

## 2026-10-07: Password sign-in stays only until Google is configured
So switching to Google cannot lock the church out. Setting the Google variables turns
passwords off. Documented in the runbook with a way back.

## 2026-10-07: Existing Q&A, snippets and calendars stay public
They have always reached the website chatbot, so migrating them to `audience="public"` changes
nothing. New items from the screens default to public too, with a visible choice to make them
staff only. Anything with an unrecognised audience value is treated as staff only.

## 2026-10-07: CSRF is enforced for every signed-in state change
Previously 8 endpoints checked it. Now a single check runs on every signed-in POST, PUT, PATCH
and DELETE, and `static/csrf.js` adds the token to every same-origin `fetch`.

## 2026-10-07: The public chatbot's CORS is limited to the church website
Other sites embedding the chat would spend the church's AI budget. Requests with no Origin
header (not a browser cross-site call) are governed by the rate limits instead.

## 2026-10-07: Rate limits live in the database
The old in-memory limiter gave each worker its own budget and forgot on every deploy.
Counters in `rate_limit_hits` are shared across workers; if the counter itself fails the chat
stays up (fail open) and the daily cap still bounds the damage.

## 2026-10-07: Staff AI chats belong to the person who had them
They were readable by every staff account. Chats now carry `user_id` and are only listed and
opened for their owner. Chats are still deleted after 14 days; the audit log is not.

## 2026-10-07: Planning Center People is not synced
Copying the membership database into another store adds risk for little gain. Phase 5 will
look a person up live, one at a time, with permission. Giving is never requested and
check-ins are counts only.

## 2026-10-07: Connectors only ever read, and webhooks never carry data
A webhook is a nudge: it is verified, then a normal sync runs. Nothing a webhook says is
trusted or stored as data.

## 2026-10-07: Streaming numbers are views, and peaks are never added
Total views are labelled as views, never people. Peak concurrent across platforms is the
highest single-platform peak, because viewers at the same moment on two platforms may overlap
and viewers cannot be de-duplicated. A person's correction beats a CSV, which beats the API,
field by field, and every correction is kept in an edit history.

## 2026-10-07: Subsplash gets manual and CSV entry, not a guessed API
No public viewership API was found and the church leaves Subsplash in April. A common
streaming source interface lets Resi or OBS plug in later.

## 2026-10-07: Meta has no peak concurrent number
The Graph API does not provide it, so Facebook peaks are left for manual entry.

## 2026-10-07: Text In Church message text is stored, narrowly
The AI is meant to read church texts, so message bodies are stored in one table, gated by
`data.text_in_church`, audited on read and kept out of raw payloads. Retention is undecided.

## 2026-10-07: Google Workspace Calendar connector deferred to Phase 3
It was marked optional and the workflows in Phase 3 are what need it.
