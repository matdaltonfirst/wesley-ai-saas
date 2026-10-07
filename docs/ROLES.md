# Roles and permissions

This is the permission matrix as built. The tables below are generated from
`permissions.py`, so they are always what the code does. Admins can change any box
on the Roles and permissions screen; those changes are saved as overrides and written
to the audit log. A box that differs from the table below was changed by an admin.

## Rules that do not depend on role

1. **Default deny.** A person can do only what one of their roles grants. A person with no
   role can sign in but sees nothing. A test fails if any route is added without a
   permission check.
2. **Sign-in** is Google Workspace, `daltonfumc.com` only. The server verifies the Google
   token (signature, audience, expiry), requires a verified email on the church domain and
   Google's own Workspace domain claim, and requires an existing active person. The browser's
   domain hint is never trusted. One person, one account; no shared logins. Sessions end
   after 8 idle hours or 24 hours in total.
3. **Excluded from the AI and the dashboard entirely, for every role including Admin:**
   pastoral care notes, background check data, children's names, and giving by donor. They
   are not permissions at all, so no role and no admin screen can grant them. A test fails
   if any such permission is ever added.
4. **Giving, aggregate and read-only,** exists as one permission (`data.giving_aggregate`) that
   **no role has**, Admin included. It does nothing until an admin turns it on for a role by
   name. Nothing reads giving data yet.
5. **Children's check-in:** aggregate counts only.
6. **Every AI use by a staff member is written to the audit log:** who, when, and which kinds
   of source and which titles were given to the AI. The question text is never stored.
   Sign-ins, role and permission changes, and denied requests are logged too. The log is
   append-only and the cleanup jobs never touch it.
7. **Writes** (create an event, send a text, publish a post, send an email) will be drafts that
   a person with the right role approves. Nothing sends or publishes automatically.
8. **The public chatbot has no role.** It runs on its own code path with its own data and can
   reach no staff data (see ARCHITECTURE.md).
9. The AI chat draws on the shared staff knowledge base for everyone who may chat. Seeing the
   knowledge-base screens is a separate permission.
10. Each person sees only their own AI chat history.

## Who holds which role (set 7 Oct 2026)

| Role | Person |
|---|---|
| Admin and Communications and Production | Mat (mat@daltonfumc.com) |
| Administrative Assistant | Carrie Ashcraft (carey@daltonfumc.com, to confirm) |
| Family Ministries | Aaron Smith (kids@daltonfumc.com) |
| Music and Worship | Matthew Dean (matthew@daltonfumc.com) |
| Pastoral | Don Landon (no address yet; added by an admin when he has one) |
| No role yet | merideth@daltonfumc.com |

## Modules

| What | Admin | Communications and Production | Administrative Assistant | Family Ministries | Music and Worship | Pastoral |
|---|---|---|---|---|---|---|
| Use the staff AI chat (`chat.use`) | yes | yes | yes | yes | yes | yes |
| See Sunday Content drafts (`sunday.read`) | yes | yes | yes | - | - | yes |
| Edit and regenerate Sunday Content (`sunday.write`) | yes | yes | - | - | - | - |
| Approve Sunday Content (`sunday.approve`) | yes | yes | - | - | - | yes |
| See event checklists and the timeline (`events.read`) | yes | yes | yes | yes | yes | yes |
| Change event checklists (`events.write`) | yes | yes | - | - | - | - |
| Submit communications requests (`requests.submit`) | yes | yes | yes | yes | yes | yes |
| See and triage the communications queue (`requests.manage`) | yes | yes | yes | - | - | - |
| See the weekly streaming numbers (`streaming.read`) | yes | yes | yes | yes | - | yes |
| Enter and correct streaming numbers (`streaming.write`) | yes | yes | yes | - | - | - |
| See guest connections (`guests.read`) | yes | yes | yes | - | - | yes |
| Update and sync guest connections (`guests.write`) | yes | - | yes | - | - | - |
| Read public chatbot conversations and analytics (`chatlogs.read`) | yes | yes | yes | - | - | yes |
| Correct public chatbot answers (`chatlogs.write`) | yes | yes | - | - | - | - |
| See the knowledge base (documents, snippets, Q&A) (`kb.read`) | yes | yes | yes | - | - | - |
| Change the knowledge base (`kb.write`) | yes | yes | - | - | - | - |
| Manage the website crawl, calendar feeds and sermon source (`sources.write`) | yes | yes | - | - | - | - |
| See the integrations status page (`integrations.read`) | yes | yes | yes | - | - | - |
| Connect and disconnect integrations (`integrations.manage`) | yes | - | - | - | - | - |
| Manage people, roles and permissions (`team.manage`) | yes | - | - | - | - | - |
| See the public chatbot instructions and local practice (`instructions.read`) | yes | - | - | - | - | yes |
| Change the public chatbot instructions and local practice (`instructions.write`) | yes | - | - | - | - | - |
| Read the audit log (`audit.read`) | yes | - | - | - | - | - |

## Data the AI and dashboards may read

| What | Admin | Communications and Production | Administrative Assistant | Family Ministries | Music and Worship | Pastoral |
|---|---|---|---|---|---|---|
| Calendar and events (`data.calendar`) | yes | yes | yes | yes | yes | yes |
| Service plans, songs and team schedules (`data.services`) | yes | yes | yes | - | yes | yes |
| Groups and memberships (`data.groups`) | yes | yes | yes | yes | - | yes |
| Event registrations (`data.registrations`) | yes | yes | yes | yes | - | yes |
| People: name, email and phone only (`data.people_basic`) | yes | yes | yes | yes | yes | yes |
| Check-in attendance, aggregate counts only (`data.checkins_counts`) | yes | yes | yes | yes | - | yes |
| Sermons and episodes (`data.sermons`) | yes | yes | yes | - | yes | yes |
| Stream and platform numbers (`data.streams`) | yes | yes | yes | yes | - | yes |
| Email campaign statistics (`data.email_stats`) | yes | yes | yes | - | - | yes |
| Text In Church contacts and conversations (`data.text_in_church`) | yes | yes | yes | - | - | yes |
| Giving, aggregate and read-only (`data.giving_aggregate`) | - | - | - | - | - | - |

"yes" means granted by default. Data connectors for most of these arrive in Phase 2; the
permissions exist now so each connector is born gated.
