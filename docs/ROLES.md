# Roles and permissions (DRAFT for review)

Status: proposal. Nothing here is built. Mark it up, then it becomes the spec for the
permission code and the admin screen.

## Rules that do not depend on role

1. **Default deny.** A role can do only what its column below says. A missing cell means no.
2. **Sign-in** is Google Workspace, `daltonfumc.com` only, verified on the server from the
   signed token (not from the browser's domain hint). One person, one account, no shared logins.
3. **Excluded from the AI and the dashboard entirely, for every role including Admin:**
   pastoral care notes and background check data. They are never loaded, indexed or shown.
4. **Children's check-in:** aggregate counts only, never names, unless you decide otherwise.
5. **Giving:** read-only, aggregate by default, behind its own explicit permission. Not part of
   any role until you grant it to a named person.
6. **Every AI data access by a staff user is written to an audit log** (who, what, when, which
   source). Retention for the log is separate from, and longer than, chat history.
7. **Writes** (create an event, send a text, publish a post, send an email) are drafts that a
   person with the right role approves. Nothing is sent or published automatically.
8. **The public chatbot** has no role. It runs on its own data set and can reach no staff data.

## Roles

| Role | Who (to confirm) | Home screen leads with |
|---|---|---|
| Admin | Mat | Integration health, who has which role, attention alerts |
| Communications and Production | Mat's day job | Review queue (Sunday Content), event checklists, requests |
| Administrative Assistant | (name?) | Weekly streaming numbers, guests awaiting follow-up, calendar |
| Family Ministries | (name?) | Their events and checklists, children's attendance aggregates, registrations |
| Music and Worship | (name?) | Service plans, team schedule, songs |
| Pastoral | Senior Pastor and clergy | This week's guests and care follow-ups (no notes), sermon content, attendance trends |

A person can hold more than one role. Permissions are the union.

## Matrix: modules

`R` read, `W` read and change, `A` approve drafts, `-` none.

| Module | Admin | Comms | Admin Asst | Family | Music | Pastoral |
|---|---|---|---|---|---|---|
| Staff AI chat (command bar) | W | W | W | W | W | W |
| Sunday Content review queue | W, A | W, A | R | - | - | R, A |
| Event checklists and timeline | W | W | R | R (own events) | R (own events) | R |
| Communications requests: submit | W | W | W | W | W | W |
| Communications requests: queue and triage | W | W | R | - | - | - |
| Streaming numbers pane | W | W | W | R | - | R |
| Streaming numbers: manual entry and edit history | W | W | W | - | - | - |
| Guest connections (website form) | W | R | W | - | - | R |
| Public chatbot: chat logs and corrections | W | W | R | - | - | R |
| Knowledge base: documents, snippets, Q&A | W | W | R | - | - | - |
| Website crawl, calendar feed, sermon source | W | W | - | - | - | - |
| Integrations status page | W | R | R | - | - | - |
| Roles and team | W | - | - | - | - | - |
| Public chatbot instructions, local practice | W | - | - | - | - | R |
| Audit log | R | - | - | - | - | - |

## Matrix: data domains the staff AI and dashboards may read

| Data domain (source) | Admin | Comms | Admin Asst | Family | Music | Pastoral |
|---|---|---|---|---|---|---|
| Calendar and events (Planning Center Calendar) | R | R | R | R | R | R |
| Service plans, songs, team schedules (Services) | R | R | R | - | R | R |
| Groups and memberships (Groups) | R | R | R | R | - | R |
| Registrations (event sign-ups) | R | R | R | R | - | R |
| People: name, email, phone only (People) | R | R | R | R (family households) | R (own team) | R |
| Check-in attendance, aggregate counts (Check-Ins) | R | R | R | R | - | R |
| Check-in names of children | - | - | - | - | - | - |
| Sermons and podcast episodes (Publishing, YouTube) | R | R | R | - | R | R |
| Stream and platform numbers (YouTube, Facebook, Subsplash) | R | R | R | R | - | R |
| Email campaign stats (Constant Contact) | R | R | R | - | - | R |
| Text In Church contacts and conversations | R | R | R | - | - | R |
| Giving, aggregate (Giving) | grant by name | - | - | - | - | grant by name |
| Giving, by donor | - | - | - | - | - | - |
| Pastoral care notes | - | - | - | - | - | - |
| Background checks | - | - | - | - | - | - |

## Questions for you

1. **Who** holds each role today? Please give the Google Workspace address for each person.
2. Should the **Senior Pastor** also be Admin, or Pastoral only? (Admin lets them change roles
   and settings; I suggest Pastoral only until they ask for more.)
3. **Text In Church conversations** contain people's messages. Is it right that the
   Administrative Assistant and Pastoral can read them through the AI, and Family Ministries
   cannot? Do you want the AI limited to counts and "awaiting follow-up" lists instead of
   message text?
4. **Family Ministries**: should they see children's names for their own classes in Planning
   Center directly (outside the AI)? That is Planning Center's own permission, not ours.
5. **Giving**: who, if anyone, should hold the giving-aggregate permission? I recommend nobody
   at first, and we build the permission and the audit log before it is used.
6. **Guest connections** hold names, emails and phone numbers. I gave write access to Admin and
   the Administrative Assistant and read to Comms and Pastoral. Correct?
7. The **audit log** is Admin-only. Should the Senior Pastor be able to read it too?
