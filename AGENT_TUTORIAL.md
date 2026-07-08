# Redmine PM Assistant - Complete Code Walkthrough

This document explains how the Redmine PM Assistant works end to end. It is written so you can understand the code yourself and also explain it to managers, peers, or engineers.

The main idea is simple:

> The LLM understands the user's request. Python code safely performs the Redmine action.

The LLM should decide intent: what the user wants, which tool to call, and whether a follow-up belongs to a previous request.

The deterministic Python code should do the safe work: validate inputs, resolve Redmine IDs, call the API, keep drafts alive, build payloads, and format results.

---

## Project Files

```text
multi_agent_redmine/
  agent.py            Main app: Redmine tools, state, CLI loop, agent setup
  config.py           ChatOpenAI / model configuration
  tool_helpers.py     Date parsing and agent-turn helper
  AGENT_TUTORIAL.md   This technical walkthrough
  PRESENTATION.md     Simple presentation/demo deck
  requirements.txt    Python dependencies
  test_*.py           Unit tests with mocked Redmine calls
  .env.example        Safe example environment variables
  .env                Local secrets, ignored by git
```

---

## What The Assistant Does

The assistant lets a PM or team lead ask Redmine questions in plain English:

```text
PM> how much billable time logged by Zohaib Hussain in Association Analytics
PM> list developers on Association Analytics
PM> draft a high priority bug: login fails on mobile
PM> log 8 hours on task 174022
```

The LLM chooses the correct tool, then the code calls Redmine and returns a safe answer.

---

## High-Level Flow

```text
User types at PM>
        |
        v
main() checks pending draft / pending clarification
        |
        v
run_agent_turn() sends message to LangChain agent
        |
        v
LLM chooses one @tool function
        |
        v
Tool validates arguments and calls Redmine API
        |
        v
Tool returns formatted text
        |
        v
Agent prints answer
```

Important: the code does not parse magic phrases like "approve" or "go ahead". Pending context is given to the LLM, and the LLM decides which tool to call next.

---

## Core Concepts

### 1. LLM Intent

The LLM decides what the user means.

Example:

```text
User: "how much billable time logged by zohaib"
```

The LLM should choose `get_user_time_logged`.

If Redmine finds multiple Zohaibs, the app remembers that the missing value is `user_name`.

Then:

```text
User: "zohaib hussain"
```

The LLM should complete the previous tool call, not start a new request.

### 2. Deterministic Tools

The tool code does not "understand language". It safely performs exact operations:

- Resolve a project name to Redmine project ID
- Resolve a user name to Redmine user ID
- Resolve an issue keyword to an issue ID
- Validate required hours/project/comment fields
- Build Redmine API payloads
- POST only structured payloads
- Format the result for the user

### 3. Pending Draft

`_pending_draft` remembers a write operation that is not submitted yet.

Used for:

- Draft issue
- Draft time entry
- Updating pending issue project
- Updating pending time entry hours/comments/billable fields
- Approving the pending draft

### 4. Pending Clarification

`_pending_clarification` remembers that a previous tool call needed one missing value.

Example:

```text
User: how much billable time logged by zohaib
Agent: Multiple users match...
User: zohaib hussain
```

The app injects context saying:

```text
Previous tool: get_user_time_logged
Missing argument: user_name
Previous arguments: project/date/issue filters
```

The LLM uses this to continue the original request.

### 5. Redmine Safety

Redmine writes are sensitive. The assistant uses guardrails:

- Default is draft first
- Missing required fields block submit
- Failed submit keeps the draft alive
- Redmine errors are shown clearly
- API keys are read from `.env`, never hardcoded

---

## `agent.py` Code Map

Use this section as your "line-by-line" guide. Instead of explaining every character, it explains every block in order.

### Lines 1-20: Imports

Purpose:

- `requests` calls Redmine API.
- `create_agent` builds the LangChain agent.
- `tool` marks Python functions as LLM-callable tools.
- `get_chat_model()` loads the configured LLM.
- `parse_date_range()` and related helpers handle date inputs.
- `run_agent_turn()` sends a user turn into the agent.

Presentation explanation:

> This section imports the API client, agent framework, and helper functions.

### Lines 22-31: Session State And Caches

Variables:

- `_users_api_search_allowed`
- `_membership_users_cache`
- `_projects_cache`
- `_priority_cache`
- `_activity_cache`
- `_time_entry_custom_field_cache`
- `_tracker_cache`
- `_pending_draft`
- `_pending_clarification`

Why they exist:

- Avoid repeated API calls.
- Keep short-term state during one terminal session.
- Remember drafts and clarification questions.

Presentation explanation:

> These are session-level memory variables. They do not store long chat history; they only store practical state needed for safe Redmine work.

### Lines 33-42: Regex And Typed Ambiguity Message

`_ISSUE_ID_REF_RE` recognizes issue numbers like `174022` or `#174022`.

`_ASSOCIATION_ANALYTICS_RE` is a local alias/fuzzy helper for a known project name typo.

`AmbiguousUserMessage` is a string subclass. It lets the code recognize user ambiguity without parsing English wording.

Why this matters:

Earlier, code might have checked whether a message started with "Multiple users match". That is phrase-dependent. Now ambiguity is typed.

### Lines 45-127: Redmine API Helpers

`_redmine_configured()` checks required env variables.

`_redmine_error()` returns a readable missing-config message.

`redmine_get(path)`:

- Builds full Redmine URL
- Adds `X-Redmine-API-Key`
- Sends GET
- Raises for HTTP errors
- Returns JSON

`redmine_post(path, payload)`:

- Builds full Redmine URL
- Adds API key
- Sends POST JSON
- Does not auto-follow redirects
- Handles manual redirects safely
- Returns JSON or readable errors

Important detail:

`allow_redirects=False` prevents a Redmine POST from being silently downgraded to GET during redirects.

### Lines 131-207: Issue Creation Helpers

`_get_current_user_id()` fetches the current Redmine user ID.

`_resolve_priority_id(priority_name)` converts text like `"High"` or `"Normal"` into a Redmine priority ID.

`_resolve_tracker_id()` finds the tracker ID for new issues.

`_default_project_identifier()` reads default project config from `.env`.

`create_issue()` builds the Redmine issue payload and posts it.

Why the LLM does not do this:

The LLM should not guess priority IDs, tracker IDs, or payload structure. Code resolves those safely.

### Lines 210-293: Draft Issue Lifecycle

`get_pending_draft()` returns the current draft.

`clear_pending_draft()` clears it after successful creation/logging.

`_resolve_draft_project()` resolves project input or default project config.

`create_issue_from_draft(draft)`:

- Checks project exists
- Resolves priority
- Resolves current user if assigning to self
- Calls `create_issue()`
- Formats success or error text

`_issue_create_succeeded()` checks whether issue creation really succeeded before clearing the draft.

Important recent fix:

If Redmine rejects issue creation, the draft is not cleared.

### Lines 297-383: Pending State Context

`_store_pending_draft()` saves a pending write draft.

`_store_pending_clarification()` saves a missing-argument clarification.

`clear_pending_clarification()` clears clarification after success.

`get_pending_clarification()` reads clarification state.

`_remember_user_clarification()` stores clarification only when user resolution returns `AmbiguousUserMessage`.

`_pending_draft_context()` creates prompt context for the LLM:

- Pending issue draft
- Pending time-entry draft
- What tool should be used to update/approve it

`_pending_clarification_context()` creates prompt context for the LLM:

- Previous tool
- Missing argument
- Previous arguments

Why this is important:

This is how the assistant supports natural follow-ups without hardcoded phrase matching.

### Lines 385-416: Formatting Drafts And Issues

`_format_draft_message()` formats a pending issue draft.

`_format_issue()` formats a Redmine issue result.

These are display helpers. They keep output consistent.

### Lines 419-629: User Resolution

This block resolves a user name to Redmine user ID.

Important functions:

- `_user_display_name()`
- `_normalize_lookup_text()`
- `_lookup_tokens()`
- `_user_identity_parts()`
- `_similarity()`
- `_get_current_user()`
- `_user_matches_name()`
- `_collect_users_from_memberships()`
- `_fuzzy_user_score()`
- `_resolve_user_fuzzy()`
- `_resolve_user()`

Flow:

1. If user says `me` or `myself`, use current Redmine user.
2. Try Redmine `/users.json?name=...`.
3. If users API is forbidden, collect users from visible project memberships.
4. Try exact/partial match.
5. Try fuzzy match.
6. If multiple users match, return `AmbiguousUserMessage`.

Why code handles this:

The LLM may understand the name, but only Redmine knows the real user ID. Code must verify it.

### Lines 632-816: Project Resolution

This block resolves project name/identifier/ID to `(project_id, project_name)`.

Resolution order:

1. Empty input means no project filter.
2. Reject date phrases mistakenly passed as project.
3. Numeric ID fetches `/projects/{id}.json`.
4. Exact name or identifier match.
5. Normalized match (`RefocusAI` vs `Refocus AI`).
6. Token match.
7. Substring match.
8. Fuzzy match.
9. If it looks like a person, suggest user-time tool instead.

Why this is needed:

Users say project names naturally. Redmine API needs project IDs.

### Lines 818-1058: Fetching And Issue Search Helpers

`_append_date_filters()` adds `from` and `to` query params.

`_fetch_project_memberships()` fetches project members with pagination.

`_is_manager_role()` checks whether a role looks like manager/lead/PM.

`_fetch_time_entries()` fetches time entries with pagination.

Issue search helpers:

- `_significant_issue_tokens()`
- `_tokenize_subject()`
- `_subject_token_matches()`
- `_score_issue_subject()`
- `_fetch_issues_by_subject_token()`
- `_collect_issue_candidates()`
- `_rank_issues_by_tokens()`
- `_resolve_issue_id_reference()`
- `_search_issues_by_keyword()`
- `_resolve_issue_ids_by_keyword()`

These functions make keyword search work without requiring exact issue titles.

### Lines 1079-1199: Time Summary And Billable Logic

`_parse_billable_custom_value()` converts values like `yes`, `no`, `1`, `0` to boolean.

`_entry_billable_status()` reads Redmine custom fields on a time entry.

`_aggregate_billable_hours()` separates:

- Billable hours
- Non-billable hours
- Unclassified hours

`_format_time_summary()` builds the time report:

- Total hours
- Entry count
- Activity breakdown
- Top issues
- Billable breakdown
- Date range

Important fix:

The assistant now says "unclassified hours" when Redmine does not expose billable status for all entries. This avoids misleading totals.

### Lines 1203-1375: Basic Redmine Tools

These are LLM-callable tools:

- `get_my_profile`
- `list_my_projects`
- `list_my_issues`
- `search_issues`
- `search_high_priority_issues`
- `get_issue`
- `get_project_manager`
- `get_project_status`

Each tool:

1. Receives arguments from the LLM.
2. Calls deterministic helper functions.
3. Returns text for the user.

### Lines 1377-1534: Time Report Tools

Tools:

- `get_my_time_logged`
- `get_user_time_logged`
- `get_project_time_logged`

They support:

- Project filter
- Date filter
- Issue keyword filter
- Billable reporting

Important flow in `get_user_time_logged()`:

If `_resolve_user()` returns `AmbiguousUserMessage`, it stores pending clarification so the next message can complete the same request.

### Lines 1545-1738: Team And Project Reporting Tools

Helpers:

- `_format_project_member_line()`
- `_fetch_user_project_memberships()`
- `_format_project_time_by_member()`

Tools:

- `list_project_members`
- `list_user_projects`
- `get_project_time_by_member`
- `get_last_logged_day`

Use case:

```text
PM> complete report with time per developer on Association Analytics
```

### Lines 1770-1878: Time Entry Custom Field Support

`_resolve_activity_id()` resolves Redmine time-entry activity.

Custom field helpers:

- `_normalize_custom_field_name()`
- `_time_entry_custom_fields()`
- `_resolve_time_entry_custom_field_id()`
- `_time_entry_custom_fields_payload()`

Purpose:

Some Redmine setups require custom fields such as:

- Billable Hours
- Time Entry Comments

The assistant can discover these from `/custom_fields.json`.

If Redmine blocks that endpoint, set:

```env
REDMINE_BILLABLE_HOURS_CUSTOM_FIELD_ID=30
REDMINE_TIME_ENTRY_COMMENTS_CUSTOM_FIELD_ID=31
```

### Lines 1881-1976: Time Entry Draft And POST Logic

`_resolve_issue_for_time_entry()` resolves issue number or keyword.

`_format_time_draft_message()` displays pending time entry draft.

`create_time_entry_from_draft()`:

- Validates hours
- Resolves activity
- Builds Redmine POST payload
- Adds custom fields if available
- Calls `redmine_post("/time_entries.json", payload)`
- Returns success or error

`_time_entry_create_succeeded()` makes sure the draft is cleared only on real success.

Important recent fix:

If Redmine rejects a time entry because Billable Hours or Time Entry Comments are missing, the draft stays alive. The user can add missing fields and approve again.

### Lines 1980-2124: Time Write Tools

Tools:

- `draft_time_entry`
- `log_time`
- `update_pending_time_entry`

`draft_time_entry`:

- Creates local draft
- Does not submit immediately

`log_time`:

- Logs immediately only when hours are provided

`update_pending_time_entry`:

- Updates hours
- Updates date
- Updates activity
- Updates comments
- Updates `billable_hours`
- Updates `time_entry_comments`

This is what fixes the flow:

```text
PM> logged now
Agent> Redmine rejected: Billable Hours and Time Entry Comments required

PM> billable hours are 8 and time entry comment is "Working on dbt automation"
Agent> updates existing pending draft

PM> approve
Agent> posts same draft successfully
```

### Lines 2130-2247: Issue Write Tools And Approval

Tools:

- `draft_issue`
- `update_pending_issue_project`
- `create_issue_in_redmine`
- `approve_pending_draft`

`draft_issue` creates a local draft.

`update_pending_issue_project` sets/changing the project.

`create_issue_in_redmine` bypasses draft when explicitly requested.

`approve_pending_draft` submits either:

- pending issue draft
- pending time-entry draft

It only clears the draft after success.

### Lines 2250-2273: Tool Registry

`ALL_TOOLS` is the registry of tools available to the LLM.

If a function is not in `ALL_TOOLS`, the LLM cannot call it as a tool.

This is the tool boundary.

### Lines 2275-2318: System Prompt

`REDMINE_SYSTEM_PROMPT` tells the LLM:

- Use tools for Redmine requests
- Do not answer Redmine facts from memory
- Use draft tools for writes
- Preserve pending draft and clarification context
- Map "billable hours" to `billable_hours`
- Map "time entry comments" to `time_entry_comments`

The prompt is intentionally concise. The detailed execution stays in Python.

### Lines 2323-2329: Build Agent

`build_redmine_agent()` calls:

```python
create_agent(
    model=get_chat_model(),
    tools=list(ALL_TOOLS.values()),
    system_prompt=REDMINE_SYSTEM_PROMPT,
)
```

This creates the LangChain agent.

### Lines 2331-End: CLI Main Loop

`main()`:

1. Checks Redmine config.
2. Prints startup message.
3. Builds the agent.
4. Reads user input.
5. Adds pending draft/clarification context if available.
6. Calls `run_agent_turn()`.
7. Prints the answer.

This is the application entry point.

---

## `tool_helpers.py` Concepts

`tool_helpers.py` contains logic that is shared by the agent.

### Date Parsing

It supports:

- `today`
- `yesterday`
- `this week`
- `last week`
- `this month`
- `last month`
- `weekend`
- `last weekend`
- `YYYY-MM-DD`
- `YYYY-MM-DD..YYYY-MM-DD`
- `from YYYY-MM-DD till today`
- `03-07-2026`

Important behavior:

For ambiguous dash dates, it prefers DD-MM-YYYY because the expected user locale is Pakistan.

Example:

```text
03-07-2026 -> 2026-07-03
```

### `is_date_range_token()`

This checks whether a string is probably a date filter instead of a project name.

This prevents mistakes like treating `last month` as a project name.

### `is_person_name_token()`

This checks whether a string looks like a person name.

It helps avoid confusing a user name with a project name.

### `run_agent_turn()`

This calls the LangChain agent:

```python
agent.invoke({"messages": [{"role": "user", "content": question}]})
```

Then it returns the final message content.

There is no regex parsing of fake tool calls anymore. LangChain handles native tool calls.

---

## `config.py` Concepts

`config.py` builds the ChatOpenAI model.

The project is OpenAI-only.

### OpenAI Model

Use:

```env
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-5.5
```

If `OPENAI_API_KEY` is missing, `get_chat_model()` raises a clear configuration error.

---

## Complete Example Flow: Time Report With Ambiguous User

User:

```text
how much billable time logged by zohaib in Association Analytics
```

Flow:

1. `main()` sends the request to the LLM.
2. LLM calls `get_user_time_logged`.
3. Tool calls `_resolve_user("zohaib")`.
4. Multiple users match.
5. `_resolve_user()` returns `AmbiguousUserMessage`.
6. `get_user_time_logged()` stores pending clarification.
7. Agent asks user to specify which Zohaib.

User:

```text
zohaib hussain
```

Flow:

1. `main()` injects pending clarification context.
2. LLM understands this is missing `user_name`.
3. LLM calls `get_user_time_logged` again with previous filters.
4. Code resolves Zohaib Hussain.
5. Code fetches time entries.
6. Code formats total, billable, non-billable, and unclassified hours.

---

## Complete Example Flow: Time Entry Rejected Then Fixed

User:

```text
Task Number is 174022 and the hours are 8
```

Flow:

1. LLM calls `draft_time_entry`.
2. Code resolves issue `174022`.
3. Code resolves default activity.
4. Code stores `_pending_draft`.
5. User sees draft.

User:

```text
logged now
```

Flow:

1. Pending draft context is injected.
2. LLM calls `approve_pending_draft`.
3. Code calls `create_time_entry_from_draft`.
4. Redmine rejects because Billable Hours and Time Entry Comments are missing.
5. Draft is not cleared.

User:

```text
billable hours are 8 and the time entry comment is "Working on the dbt automation"
```

Flow:

1. Pending draft context is injected.
2. LLM calls `update_pending_time_entry`.
3. Code updates `billable_hours` and `time_entry_comments`.
4. User approves again.
5. Code builds custom fields payload and posts to Redmine.
6. Draft clears only after success.

---

## Why LLM Does Not Do Everything

The LLM should decide:

- What the user wants
- Which tool to call
- Whether a follow-up belongs to a previous request
- Which arguments to pass

The Python code should decide:

- Whether Redmine config exists
- Whether API calls succeed
- Whether a project/user/issue actually exists
- How to build Redmine JSON payloads
- How to handle required fields
- When to keep or clear drafts

Reason:

The LLM can understand language, but it should not be trusted to invent IDs, payloads, or write operations.

---

## Tool List

### Profile And Project Tools

- `get_my_profile`
- `list_my_projects`
- `list_project_members`
- `list_user_projects`
- `get_project_manager`
- `get_project_status`

### Issue Tools

- `list_my_issues`
- `search_issues`
- `search_high_priority_issues`
- `get_issue`

### Time Report Tools

- `get_my_time_logged`
- `get_user_time_logged`
- `get_project_time_logged`
- `get_project_time_by_member`
- `get_last_logged_day`

### Write Tools

- `draft_issue`
- `update_pending_issue_project`
- `create_issue_in_redmine`
- `draft_time_entry`
- `update_pending_time_entry`
- `log_time`
- `approve_pending_draft`

Total: 22 tools.

---

## Environment Variables

Required:

```env
REDMINE_URL=https://redmine.example.com
REDMINE_API_KEY=your-redmine-api-key
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.5
```

Optional Redmine defaults:

```env
REDMINE_PROJECT_ID=18
REDMINE_DEFAULT_PROJECT_ID=1576
REDMINE_DEFAULT_TRACKER_ID=3
REDMINE_DEFAULT_ACTIVITY_ID=9
```

Optional time-entry custom field IDs:

```env
REDMINE_BILLABLE_HOURS_CUSTOM_FIELD_ID=30
REDMINE_TIME_ENTRY_COMMENTS_CUSTOM_FIELD_ID=31
```

---

## How To Run

```powershell
.\.venv\Scripts\python.exe agent.py
```

Then type:

```text
PM> list my projects
PM> how much time logged in Association Analytics
PM> how much billable time logged by zohaib
PM> zohaib hussain
PM> log 8 hours on task 174022
```

---

## How To Test

```powershell
.\.venv\Scripts\python.exe -m pytest . -q
```

Current expected result:

```text
87 passed
```

---

## Deep Technical Reference

This section is for understanding the code like an engineer who may need to debug, extend, or explain the implementation.

---

## Runtime Data Structures

### Pending Issue Draft Shape

When `draft_issue()` is called, the app stores a dictionary in `_pending_draft`.

Shape:

```python
{
    "kind": "issue",
    "title": "Login fails on mobile",
    "description": "Steps/details from user",
    "priority": "High",
    "project_id": 1576,
    "project_name": "Association Analytics",
    "project_identifier_or_id": "Association Analytics",
    "assign_to_me": True,
}
```

Meaning:

- `kind` tells `approve_pending_draft()` whether this is an issue or time entry.
- `project_id` is required before creating the issue.
- `assign_to_me` decides whether `_get_current_user_id()` is called.

Lifecycle:

```text
draft_issue()
  -> _store_pending_draft()
  -> update_pending_issue_project() if project changes
  -> approve_pending_draft()
  -> create_issue_from_draft()
  -> clear_pending_draft() only if create succeeded
```

### Pending Time Entry Draft Shape

When `draft_time_entry()` is called, the app stores:

```python
{
    "kind": "time_entry",
    "issue_id": 174022,
    "issue_subject": "Automate dbt flow",
    "hours": 8,
    "spent_on": "2026-07-08",
    "activity": "",
    "activity_id": 9,
    "activity_name": "Development",
    "comments": "native Redmine note",
    "billable_hours": 8,
    "time_entry_comments": "Working on the dbt automation",
}
```

Meaning:

- `comments` is Redmine's native time-entry comments field.
- `time_entry_comments` is the custom field named "Time Entry Comments".
- `billable_hours` is the custom field named "Billable Hours".
- `activity_id` is required by Redmine.

Lifecycle:

```text
draft_time_entry()
  -> _build_time_entry_draft()
  -> _store_pending_draft()
  -> update_pending_time_entry() if user adds hours/comment/billable fields
  -> approve_pending_draft()
  -> create_time_entry_from_draft()
  -> clear_pending_draft() only if POST succeeded
```

### Pending Clarification Shape

When a tool cannot continue because a value is ambiguous, the app stores:

```python
{
    "tool": "get_user_time_logged",
    "missing_argument": "user_name",
    "arguments": {
        "project_identifier_or_id": "Association Analytics",
        "date_range": "",
        "issue_keyword": "",
    },
    "message": "Multiple users match 'zohaib': Zohaib Hussain, zohaib Ali..."
}
```

Meaning:

- The next user message is likely the missing argument.
- The previous filters must be preserved.
- The LLM receives this context and decides whether to continue or start a new request.

Lifecycle:

```text
get_user_time_logged("zohaib")
  -> _resolve_user()
  -> AmbiguousUserMessage
  -> _remember_user_clarification()
  -> next main() turn injects _pending_clarification_context()
  -> LLM calls get_user_time_logged("zohaib hussain", previous filters)
  -> clear_pending_clarification()
```

---

## State Machines

### Issue Draft State Machine

```text
No draft
  |
  | draft_issue()
  v
Issue draft pending
  |
  | update_pending_issue_project()
  v
Issue draft updated
  |
  | approve_pending_draft()
  v
Create attempt
  |
  | success                 | Redmine error
  v                         v
No draft             Issue draft still pending
```

The key rule is: a failed create does not clear the draft.

### Time Entry Draft State Machine

```text
No draft
  |
  | draft_time_entry()
  v
Time entry draft pending
  |
  | update_pending_time_entry()
  v
Time entry draft updated
  |
  | approve_pending_draft()
  v
POST /time_entries.json
  |
  | success                 | Redmine validation error
  v                         v
No draft             Time entry draft still pending
```

This is why the user can fix missing Billable Hours or Time Entry Comments after Redmine rejects the first submit.

### Clarification State Machine

```text
No clarification
  |
  | ambiguous user
  v
Clarification pending
  |
  | user provides missing value
  v
Original tool is called again
  |
  | success
  v
No clarification
```

The code remembers the missing argument, but the LLM decides whether the next user message completes it.

---

## Main Loop In Detail

`main()` is the runtime controller.

Pseudo-code:

```python
if Redmine config is missing:
    print error and stop

agent = build_redmine_agent()

while True:
    question = input("PM> ")

    if question is quit:
        break

    context_parts = [
        _pending_draft_context(),
        _pending_clarification_context(),
    ]

    if any context exists:
        question_for_agent = context + "\n\nUser reply: " + question
    else:
        question_for_agent = question

    answer = run_agent_turn(agent, question_for_agent, ALL_TOOLS)
    print answer
```

Why this design works:

- The app does not keep full chat history.
- Only actionable workflow state is carried forward.
- The LLM gets enough context to make the next decision.
- Sensitive operations remain inside deterministic tools.

---

## Tool Calling Contract

Every `@tool` function follows the same contract:

```text
Input: structured arguments from the LLM
Output: user-facing string
Side effects: optional Redmine API call or pending state update
```

Examples:

```python
get_user_time_logged(
    user_name="Zohaib Hussain",
    project_identifier_or_id="Association Analytics",
    date_range="",
    issue_keyword="",
)
```

Returns:

```text
Project: Association Analytics
Total hours (Zohaib Hussain): 646.75
...
```

Another example:

```python
update_pending_time_entry(
    billable_hours=8,
    time_entry_comments="Working on the dbt automation",
)
```

Returns:

```text
DRAFT TIME ENTRY (not logged yet)
...
Billable Hours: 8
Time Entry Comments: Working on the dbt automation
```

Important:

The tool returns a string. It does not return raw JSON to the user.

---

## Redmine API Contract

### GET Helper

`redmine_get(path)` expects a relative Redmine API path:

```python
redmine_get("/users/current.json")
redmine_get("/issues/174022.json")
redmine_get("/time_entries.json?user_id=me")
```

It adds:

```http
X-Redmine-API-Key: <REDMINE_API_KEY>
Content-Type: application/json
```

It returns parsed JSON.

### POST Helper

`redmine_post(path, payload)` sends JSON:

```python
redmine_post("/issues.json", {"issue": issue_body})
redmine_post("/time_entries.json", {"time_entry": time_entry_body})
```

It intentionally does not auto-follow redirects because `requests` can downgrade POST to GET during redirect handling.

---

## Issue Creation Payload

`create_issue()` builds:

```python
{
    "issue": {
        "project_id": 1576,
        "subject": "Login fails on mobile",
        "description": "Details...",
        "assigned_to_id": 878,
        "priority_id": 2,
        "tracker_id": 3,
    }
}
```

Where values come from:

- `project_id`: `_resolve_project()`
- `assigned_to_id`: `_get_current_user_id()`
- `priority_id`: `_resolve_priority_id()`
- `tracker_id`: `_resolve_tracker_id()`

The LLM does not create this payload. It only calls the tool with high-level arguments.

---

## Time Entry Payload

`create_time_entry_from_draft()` builds:

```python
{
    "time_entry": {
        "issue_id": 174022,
        "hours": 8,
        "spent_on": "2026-07-08",
        "activity_id": 9,
        "comments": "native Redmine note",
        "custom_fields": [
            {"id": 30, "value": "8"},
            {"id": 31, "value": "Working on the dbt automation"}
        ]
    }
}
```

Custom fields are added only when available:

- `billable_hours` -> Billable Hours custom field
- `time_entry_comments` -> Time Entry Comments custom field

Field IDs are resolved by:

1. `.env` override, if present
2. `/custom_fields.json`, if visible

---

## Custom Field Resolution

The helper `_resolve_time_entry_custom_field_id()` does this:

```text
Check env var first
  |
  v
If env var is numeric, use it
  |
  v
Otherwise fetch /custom_fields.json
  |
  v
Filter fields where customized_type == "time_entry"
  |
  v
Match names such as "Billable Hours" or "Time Entry Comments"
```

Why env override exists:

Some Redmine users cannot access `/custom_fields.json`. In that case, set the field IDs manually.

---

## Error Handling Rules

### Read Errors

Most read tools catch:

```python
requests.RequestException
RuntimeError
KeyError
```

And return:

```text
Redmine error: ...
```

### Write Errors

`redmine_post()` formats Redmine validation errors:

```text
Redmine rejected the request (HTTP 422): Billable Hours cannot be blank
```

### Draft Clearing Rule

Drafts clear only after confirmed success.

For issue creation:

```python
_issue_create_succeeded(answer)
```

For time entry creation:

```python
_time_entry_create_succeeded(answer)
```

If Redmine rejects the POST, the draft stays pending.

---

## Resolver Strategy

### User Resolver

`_resolve_user()` is responsible for turning a human name into a Redmine user ID.

Resolution path:

```text
empty -> error
me/myself/current user -> /users/current.json
Redmine users API -> exact/partial/fuzzy match
if users API forbidden -> scan project memberships
multiple matches -> AmbiguousUserMessage
no match -> "No user found"
```

Why typed ambiguity:

`AmbiguousUserMessage` lets code identify ambiguity without checking English message text.

### Project Resolver

`_resolve_project()` turns project input into `(project_id, project_name)`.

Resolution path:

```text
empty -> None
date-looking token -> error
numeric ID -> /projects/{id}.json
exact name/identifier
normalized name
token match
substring match
fuzzy match
person-name fallback warning
```

This is deterministic. The LLM does not guess project IDs.

### Issue Resolver

`_resolve_issue_for_time_entry()` handles:

```text
174022
#174022
dbt automation
this task
```

If multiple issues match, it asks for an issue number.

---

## Time Reporting Calculation

`_format_time_summary()` receives a list of Redmine time entries.

It calculates:

```python
total_hours = sum(entry["hours"])
```

It groups by activity:

```text
Development: 8.00h
Meeting: 2.00h
```

It groups by issue:

```text
#174022: 8.00h
#174099: 3.00h
```

It calculates billable status:

```text
billable_hours
non_billable_hours
unclassified_hours
```

Unclassified means the entry has no recognizable billable custom field.

---

## Date Parsing Internals

`parse_date_range()` converts natural language into API date filters.

Examples:

```text
today -> 2026-07-08 to 2026-07-08
yesterday -> previous day
this week -> Monday to Sunday
last weekend -> previous Saturday to Sunday
03-07-2026 -> 2026-07-03
from 2026-06-08 till today -> 2026-06-08 to current date
```

Important detail:

Ambiguous dash dates prefer DD-MM-YYYY.

This matters because:

```text
03-07-2026
```

means:

```text
3 July 2026
```

not March 7.

---

## How To Debug A User Request

Use this checklist.

### 1. What did the LLM choose?

Look at the user request and map it to one tool in `ALL_TOOLS`.

Example:

```text
how much billable time logged by zohaib
```

Expected tool:

```text
get_user_time_logged
```

### 2. Did state context exist?

Check:

- `_pending_draft`
- `_pending_clarification`

If a draft exists, the next user reply should usually update or approve it.

### 3. Did resolver return an error?

Possible resolver failures:

- Multiple users
- Multiple projects
- Multiple issues
- Date passed as project
- Person name passed as project

### 4. Did Redmine reject the write?

Look for messages like:

```text
Redmine rejected the request (HTTP 422): ...
```

If write failed, confirm draft is still pending.

### 5. Does `.env` need custom field IDs?

If error mentions:

```text
Billable Hours
Time Entry Comments
```

and `/custom_fields.json` is forbidden, set:

```env
REDMINE_BILLABLE_HOURS_CUSTOM_FIELD_ID=...
REDMINE_TIME_ENTRY_COMMENTS_CUSTOM_FIELD_ID=...
```

---

## Test Coverage Map

The tests protect important behavior:

- `test_routing.py`: all 22 tools are registered and agent builds.
- `test_tool_helpers.py`: date parsing and project resolution.
- `test_new_tools.py`: user/project/member tools, billable reporting, clarification memory.
- `test_time_logging.py`: draft time entry, approve flow, custom fields, rejected draft persistence.
- `test_issue_creation.py`: draft issue, approve flow, tracker ID, rejected issue behavior.
- `test_session_fixes.py`: search, issue filtering, session fixes.
- `test_last_logged_day.py`: most recent time entry behavior.
- `test_fixes.py`: regression tests for earlier bugs.

When changing code, always run:

```powershell
.\.venv\Scripts\python.exe -m pytest . -q
```

---

## Extension Points

### Add A New Read Tool

Steps:

1. Write a helper that calls Redmine.
2. Add an `@tool` function.
3. Return a user-facing string.
4. Add it to `ALL_TOOLS`.
5. Add routing guidance to `REDMINE_SYSTEM_PROMPT` if needed.
6. Add tests.

### Add A New Write Tool

Steps:

1. Prefer draft-first design.
2. Store data in `_pending_draft`.
3. Add an update tool if fields may be missing.
4. Submit only through an approve tool.
5. Clear draft only after success.
6. Add tests for rejection and recovery.

### Add A New Custom Field

Steps:

1. Add a draft field.
2. Add it to `update_pending_time_entry`.
3. Add custom field ID resolution.
4. Add it to `_time_entry_custom_fields_payload`.
5. Add `.env.example` docs.
6. Add tests with env field IDs.

---

## How To Explain This In Presentation

Use this simple explanation:

> We built a Redmine assistant. The user asks in natural language. The LLM understands the intent and chooses a safe tool. The tool code validates inputs, resolves Redmine IDs, calls the live API, and returns a formatted answer. For write actions, it drafts first and only submits after approval. If Redmine rejects required fields, the draft stays alive so the user can fix it.

Short version:

> LLM decides. Code executes safely.

---

## Recommended Presentation Invite Title

Best title:

```text
Redmine PM Assistant Demo: Natural Language Reporting and Safe Updates
```

Other options:

- AI Assistant for Redmine Project Management
- Redmine PM Copilot: Live Demo and Workflow Overview
- Natural Language Redmine Assistant for PMs and Engineering

---

## Common Questions And Answers

### Why not let the LLM call Redmine directly?

Because Redmine writes need validation, IDs, required fields, and safe payloads. The LLM should choose intent, not invent API payloads.

### Why keep pending draft state?

Because users often give write details across multiple turns. The assistant must remember the draft until it succeeds or the session ends.

### Why keep pending clarification state?

Because names are often ambiguous. If the user clarifies "Zohaib Hussain", the assistant should continue the previous request, not start a new one.

### Why track unclassified billable hours?

Because Redmine may not expose billable status for every time entry. Showing unclassified hours prevents misleading reports.

### Why do we still have resolver code if the LLM is smart?

Because Redmine requires exact IDs. The LLM understands language; code verifies reality.
