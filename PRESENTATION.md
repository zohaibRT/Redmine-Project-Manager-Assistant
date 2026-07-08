---
marp: true
theme: gaia
paginate: true
backgroundColor: #0f172a
color: #e2e8f0
style: |
  section {
    font-family: "Segoe UI", Arial, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
  }
  section.lead {
    text-align: center;
    justify-content: center;
  }
  h1, h2 {
    color: #5eead4;
    border-bottom: 2px solid #14b8a6;
    padding-bottom: 0.2em;
  }
  section.lead h1 {
    border-bottom: none;
    font-size: 2.4em;
  }
  strong { color: #5eead4; }
  code {
    background: #1e293b;
    color: #fde68a;
    padding: 0.1em 0.35em;
    border-radius: 4px;
  }
  pre {
    background: #0b1120;
    border: 1px solid #334155;
    border-radius: 8px;
    font-size: 0.82em;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9em;
  }
  th {
    background: #134e4a;
    color: #ccfbf1;
    padding: 0.45em;
  }
  td {
    border-bottom: 1px solid #334155;
    padding: 0.45em;
  }
header: Redmine PM Assistant
footer: Redmine PM Assistant
---

<!-- _class: lead -->

# Redmine PM Assistant

### Ask Redmine questions in plain English

Query projects, issues, time logs, and safely draft updates.

---

## What We Built

A command-line assistant connected to live Redmine.

Instead of opening Redmine screens and filters, the user asks:

```text
How much billable time did Zohaib log in Association Analytics?
```

The assistant understands the request, checks Redmine, and returns a clear answer.

---

## Why It Helps

| Current workflow | With assistant |
|---|---|
| Search projects manually | Ask by project name |
| Build time filters manually | Ask date or time questions directly |
| Remember issue IDs | Search by words or issue number |
| Risky create/log actions | Draft first, then approve |
| Ambiguous names | Assistant asks and continues |

---

## Main Use Cases

- Check project and team information
- Search issues and assigned work
- Report time by person, project, date, or issue
- See billable, non-billable, and unclassified hours
- Draft new Redmine issues
- Draft or log time entries

---

## How It Works

1. User asks a question in the terminal.
2. LLM understands the intent.
3. Assistant calls the correct Redmine action.
4. Redmine returns live data.
5. Assistant formats the answer.

The LLM decides what the user means.
The code safely performs the Redmine work.

---

## Safety

For write actions, the assistant does not blindly post to Redmine.

- New issues are drafted first
- Time entries are drafted first when details are missing
- Missing project or hours blocks the write
- User can review before creating or logging
- Immediate write is only used when clearly requested

---

## Example: Time Report

```text
PM> how much billable time logged by zohaib hussain in Association Analytics

[Redmine Agent]:
Total hours (Zohaib Hussain): 646.75

Billable breakdown for classified entries:
19.00h billable, 41.00h non-billable

Unclassified hours: 586.75h
```

This avoids hiding hours where Redmine has no billable status.

---

## Example: Clarification

```text
PM> how much billable time logged by zohaib

[Redmine Agent]:
Multiple users match: Zohaib Hussain, zohaib Ali.
Please specify which one.

PM> zohaib hussain

[Redmine Agent]:
Continues the previous time question for Zohaib Hussain.
```

The assistant remembers the previous question.

---

## Example: Draft Before Create

```text
PM> draft a high priority bug: login fails on mobile

[Redmine Agent]:
DRAFT ISSUE (not created yet)
Title: Login fails on mobile
Priority: High
Project: not specified

PM> use Association Analytics and create it

[Redmine Agent]:
Created issue #12345
```

---

## Live Demo Plan

Run these in order:

```text
1. list my projects
2. how much time logged in Association Analytics
3. how much billable time logged by zohaib
4. zohaib hussain
5. list developers on Association Analytics
6. draft a task: update project documentation
```

This shows read, reporting, clarification, team lookup, and safe write flow.

---

## How To Run

Create `.env`:

```env
REDMINE_URL=https://redmine.example.com
REDMINE_API_KEY=your-redmine-key
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-5.5
```

Run:

```powershell
.\.venv\Scripts\python.exe agent.py
```

---

## Current Status

- Connected to live Redmine API
- Uses LLM tool calling
- 22 Redmine actions available
- Draft and approval flow implemented
- Clarification memory implemented
- 84 automated tests passing

---

## What Is Next

- Use a stronger reasoning model for better routing
- Add more real-world demo questions
- Add CSV/export for reports
- Add a simple web or Teams interface
- Add role-based permission controls

---

<!-- _class: lead -->

# Demo

### Let us run it and show the assistant working
