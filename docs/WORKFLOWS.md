# Workflows

Phase 3 adds three things that turn the data from Phase 2 into work people actually do: event
checklists, the weekly sermon content review queue, and the Needs attention list. None of them send
or publish anything. They are lists a person works through.

## Event checklists (`/checklists`)

**What it does.** When an event appears in Planning Center, it gets a checklist of what has to happen
before it. Each task has an owner role (Communications, Family Ministries and so on) and a due date
counted back from the event.

**How an event gets a checklist.**
1. A **template** is a named list of tasks ("Community event", "Youth or kids event", "Special worship
   service", "Dinner or fundraiser" to start). Each task has a role and a number of days before the
   event. A negative number means after it.
2. A template has **keywords**. An event matches a template when any keyword appears in its name or its
   Planning Center tags (not case sensitive). A template with no keywords never matches anything.
3. Every 20 minutes the app looks at events in the next 60 days and makes one checklist per event and
   matching template. Someone with permission to manage events can also press **Check Planning Center
   now**, or make one by hand with **New checklist**.

**Rules it keeps.**
- Running it again never makes a second checklist for the same event and template.
- If an event moves in Planning Center, the due dates of open tasks move with it. A task that is done,
  or whose date a person typed, is left alone.
- If an event disappears from Planning Center before it happens, its checklist is cancelled. An event
  that has already happened is simply over, not cancelled.
- An event added late does not start with a pile of overdue work. Tasks whose natural due date had
  already passed are marked "catch up" and due today, and only count as overdue once the event is a week
  away or less.
- At most 25 new checklists are made in one pass, so a template that is too broad cannot flood the page.

**Who can do what.** Anyone who can see events can read checklists. A task can be ticked by the person it
is assigned to or anyone holding its owner role. Changing templates, adding or removing tasks, changing
due dates and assigning people needs the permission to change event checklists (Admin and Communications
by default). Every change is written to the audit log.

**Changing the starter templates.** Press **Edit templates**. They are examples, not Dalton First's
process. Edit them to match how events really run here. The starter set is only created the first time a
person who can manage events opens the page and there are no templates at all.

**Where the code is.** `checklists.py` (the engine), `routes/checklists_routes.py`,
`templates/checklists.html`, `static/checklists.js`. The scheduled job is `checklist_refresh_job` in `app.py`.

## Sermon content review (`/review`)

**What it does.** Each sermon's weekly content is five pieces: YouTube title, description and chapters;
quotes; social posts; the blog post; the small group guide. Every piece has its own status.

| Status | Meaning |
|---|---|
| Draft | Built or changed, nobody has approved it |
| Changes requested | A reviewer said what needs to change (a note is required) |
| Approved | A person looked at these exact words and approved them |

**Rules it keeps.**
- **Nothing publishes or sends.** There is no publish action anywhere. Approval is a record.
- **An edit voids an approval.** Edit or rebuild a piece and it goes back to draft.
- **Nothing is lost.** The words a piece had before an edit or a rebuild are saved as a version (the last
  ten per piece) and can be put back.
- **Quotes and chapters cannot be edited,** because their value is that they are provably what was
  preached and where. They can be rebuilt.
- **A failed rebuild changes nothing,** and an empty one is refused.

**Who can do what.** Reading needs the Sunday content read permission. Editing and rebuilding need the
write permission. Approving needs the approve permission (Admin, Communications and Pastoral by default).
Asking for changes needs approve or write.

**Where the code is.** `review.py`, `routes/review_routes.py`, `templates/review.html`, `static/review.js`.
The older editor (`PATCH /api/packets/<id>`) goes through the same rules.

## Needs attention (home page)

**What it does.** A list of things that are actually wrong or overdue, for the person looking. It is worked
out from current data each time, so the same problem is always the same alert and is never repeated. A
person can hide one for a week (up to 30 days).

| Alert | Who sees it |
|---|---|
| A connection is failing, needs reconnecting, or has not synced recently | People who can see Integrations |
| Overdue checklist tasks (grouped per checklist) | The person a task belongs to; managers see all |
| A sermon waiting for approval | People who can approve |
| Changes requested on a sermon | People who can edit |
| No streaming numbers for last Sunday (from Monday) | People who enter streaming numbers |
| Guest connections waiting more than two days | People who can see guests |

**Not built yet.** Text In Church "waiting for a reply" alerts. Text In Church does not tell us which
conversation belongs to which person yet (see INTEGRATIONS.md), and a list that wrongly showed everyone as
unanswered would be worse than no list.

**Where the code is.** `attention.py`, `routes/attention_routes.py`, `static/home_attention.js`.
