from typing import Literal

from langchain.tools import tool
from pydantic import BaseModel, Field

from redmine_client import redmine_get

_BILLABLE_HOURS_CUSTOM_FIELD_ID = 1

IssueInclude = Literal[
    "children",
    "attachments",
    "relations",
    "changesets",
    "journals",
    "watchers",
    "allowed_statuses",
]

class GetIssueInput(BaseModel):
    issue_id: int = Field(
        ...,
        gt=0,
        description=(
            "The positive numeric Redmine issue ID. "
            "For an issue written as #125, provide 125."
        ),
    )

    include: list[IssueInclude] = Field(
        default_factory=list,
        description=(
            "Optional associated issue information to retrieve, such as "
            "journals, attachments, relations, children, or watchers."
        ),
    )

class GetIssueTimeSummaryInput(BaseModel):
    issue_id: int = Field(
        ...,
        gt=0,
        description=(
            "The numeric Redmine issue ID whose total and billable "
            "logged hours should be retrieved."
        ),
    )

class ListIssueInput(BaseModel):
    project_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional numeric Redmine project ID. Leave empty when the "
            "request is not limited to a specific project."
        ),
    )

    assigned_to_id: str | int | None = Field(
        default=None,
        description=(
            "Optional assignee filter. Use 'me' for the authenticated user "
            "or provide a numeric Redmine user ID."
        ),
    )

    status_id: str | int = Field(
        default="open",
        description=(
            "Use 'open', 'closed', '*', or a numeric Redmine status ID."
        )
    )

@tool(
        description=(
            "Get the Redmine profile of the currently authenticated user. "
            "Use this tool when the user asks questions such as 'Who am I?', "
            "'Show my Redmine profile', 'What is my Redmine email?', or "
            "'What is my Redmine login?'. "
            "Do not use this tool to search for another user, list project members, "
            "or retrieve project information."
        )
)
def get_my_profile()-> dict:
    """
    Get the current user's profile information from Redmine.
    """
    data = redmine_get("/users/current.json")
    user = data.get("user", {})

    return {
        "id": user.get("id"),
        "login": user.get("login"),
        "firstname": user.get("firstname"),
        "lastname": user.get("lastname"),
        "mail": user.get("mail"),
        "last_login_on": user.get("last_login_on"),
        "created_on": user.get("created_on"),
    }

@tool(
    description=(
        "List the Redmine projects that the currently authenticated user "
        "is a member of. Use this tool when the user asks for 'my projects', "
        "'projects I belong to', or whether they are a member of a specific "
        "named project. For questions such as 'Am I a member of EME?', call "
        "this tool immediately and inspect the returned project names. "
        "Read-only access does not require confirmation. "
        "Do not use this tool for another user's projects, Redmine groups, "
        "or detailed project status. "
        "Also use this tool to resolve a project name to its numeric project ID "
        "before retrieving issues for that project. "
    )
)
def list_my_projects()-> dict:
    """
    Fetch projects associated with the authenticated Redmine user.
    """
    data = redmine_get("users/current.json", params={"include":"memberships"})
    
    memberships = data.get("user", {}).get("memberships", [])
    projects = []

    for membership in memberships:
        project = membership.get("project", {})
        if project:
            projects.append({
                    "id": project.get("id"),
                    "name": project.get("name"),
                })
    return {
        "project_count": len(projects),
        "projects": projects
        }

@tool(
    args_schema=GetIssueInput,
    description=(
        "Retrieve complete read-only details about one Redmine issue using "
        "its numeric issue ID. The result includes project, tracker, status, "
        "priority, author, assignee, subject, description, dates, logged hours, "
        "and every issue custom field, including fields with empty values. "
        "Use this tool for issue summaries, issue details, status questions, "
        "assignee questions, or requests to list an issue's custom fields. "
        "Do not use this tool when the user provides only a keyword, subject, "
        "or project name without an issue ID. "
        "Use the optional include input when the user requests issue history, "
        "comments, journals, attachments, relations, children, watchers, "
        "changesets, or allowed statuses. "
        "For issue comments or change history, request journals."
    ),
)
def get_issue(
    issue_id: int,
    include: list[IssueInclude] | None = None,
    )->dict:
    """Fetch one Redmine issue using its numeric ID."""

    params = None

    if include:
        params = {"include": ",".join(include)}
    data = redmine_get(f"issues/{issue_id}.json", params=params)
    issue = data.get("issue")

    if not isinstance(issue, dict):
        raise RuntimeError(f"Redmine issue {issue_id} not found.")

    custom_fields = []

    for field in issue.get("custom_fields", []):
        custom_fields.append(
            {
                "id": field.get("id"),
                "name": field.get("name"),
                "value": field.get("value"),
            }
        )   

    result = {
        "id": issue.get("id"),
        "subject": issue.get("subject"),
        "description": issue.get("description"),
        "project": issue.get("project", {}).get("name"),
        "tracker": issue.get("tracker", {}).get("name"),
        "status": issue.get("status", {}).get("name"),
        "priority": issue.get("priority", {}).get("name"),
        "author": issue.get("author", {}).get("name"),
        "assigned_to": (issue.get("assigned_to") or {}).get("name"),
        "category": (issue.get("category") or {}).get("name"),
        "start_date": issue.get("start_date"),
        "due_date": issue.get("due_date"),
        "completion_percentage": issue.get("done_ratio"),
        "estimated_hours": issue.get("estimated_hours"),
        "spent_hours": issue.get("spent_hours"),
        "custom_fields": custom_fields,
        "created_on": issue.get("created_on"),
        "updated_on": issue.get("updated_on"),
    }

    for section in include or []:
        result[section] = issue.get(section, [])

    return result

@tool(
    args_schema=GetIssueTimeSummaryInput,
    description=(
        "Get total logged hours, billable hours, and non-billable hours "
        "for a specific Redmine issue. Use this when the user asks about "
        "time logged or billable time for an issue."
    ),
)
def get_issue_time_summary(issue_id: int) -> dict:
    """Calculate the time summary for a Redmine issue."""

    time_entries = []
    limit = 100
    offset = 0

    while True:
        data = redmine_get(
            "/time_entries.json",
            params={
                "issue_id": issue_id,
                "limit": limit,
                "offset": offset,
            },
        )

        current_entries = data.get("time_entries", [])
        time_entries.extend(current_entries)

        total_count = data.get("total_count", len(time_entries))

        if not current_entries or len(time_entries) >= total_count:
            break

        offset += len(current_entries)

    total_hours = sum(
        float(entry.get("hours") or 0)
        for entry in time_entries
    )

    billable_hours = 0.0

    for entry in time_entries:
        for custom_field in entry.get("custom_fields", []):
            if custom_field.get("id") == _BILLABLE_HOURS_CUSTOM_FIELD_ID:
                billable_hours += float(custom_field.get("value") or 0)
                break

    non_billable_hours = total_hours - billable_hours

    return {
        "issue_id": issue_id,
        "entry_count": len(time_entries),
        "total_hours": round(total_hours, 2),
        "billable_hours": round(billable_hours, 2),
        "non_billable_hours": round(non_billable_hours, 2),
    }

@tool (
    args_schema=ListIssueInput,
    description=(
        "List Redmine issues using optional project, assignee, and status filters. "
        "If the user provides a project name without its numeric ID, first use "
        "the available project-listing tool to find the matching project ID, "
        "then call this tool with that project_id. "
        "Whenever the user asks for 'my issues', 'my open issues', 'my tasks', "
        "or issues assigned to them, set assigned_to_id to exactly 'me'. "
        "Use status_id='open' for open issues, 'closed' for closed issues, "
        "and '*' for all statuses."
    ),
)
def list_issues(
    project_id: int | None = None,
    assigned_to_id: str | int | None = None,
    status_id: str | int = "open",
) -> dict:
    """Retrieve matching Redmine issues."""

    params = {
        "status_id": status_id,
        "limit": 100,
        "sort": "updated_on:desc",
    }

    if project_id is not None:
        params["project_id"] = project_id

    if assigned_to_id is not None:
        params["assigned_to_id"] = assigned_to_id

    data = redmine_get("/issues.json", params=params)

    issues = []

    for issue in data.get("issues", []):
        issues.append(
            {
                "id": issue.get("id"),
                "subject": issue.get("subject"),
                "project": issue.get("project", {}).get("name"),
                "tracker": issue.get("tracker", {}).get("name"),
                "status": issue.get("status", {}).get("name"),
                "priority": issue.get("priority", {}).get("name"),
                "assigned_to": (issue.get("assigned_to") or {}).get("name"),
                "due_date": issue.get("due_date"),
                "done_ratio": issue.get("done_ratio"),
                "updated_on": issue.get("updated_on"),
            }
        )

    total_count = data.get("total_count", len(issues))


    return {
        "applied_filters": {
            "project_id": project_id,
            "assigned_to_id": assigned_to_id,
            "status_id": status_id,
        },
        "has_results": len(issues) > 0,
        "total_count": total_count,
        "returned_count": len(issues),
        "issues": issues,
    }
