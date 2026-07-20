import os

import requests


def get_redmine_config() -> tuple[str, str]:
    redmine_url = os.getenv("REDMINE_URL", "").strip()
    redmine_api_key = os.getenv("REDMINE_API_KEY", "").strip()

    if not redmine_url or not redmine_api_key:
        raise RuntimeError(
            "Redmine URL and API key are required."
        )

    return redmine_url.rstrip("/"), redmine_api_key

def _redmine_request(
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
    
    redmine_url, redmine_api_key = get_redmine_config()
    request_url = f"{redmine_url}/{path.lstrip('/')}"

    headers = {
        "X-Redmine-API-Key": redmine_api_key,
        "Accept": "application/json",
    }

    if json_body is not None:
        headers["Content-Type"] = "application/json"

    response = requests.request(
        method=method,
        url=request_url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=30,
    )
    response.raise_for_status()

    if not response.content:
        return {}
    
    return response.json()

def redmine_get(
    path: str,
    params: dict | None = None,
) -> dict:

    return _redmine_request(
        method="GET",
        path=path,
        params=params,
    )

def redmine_post(
        path: str,
        json_body: dict | None = None,
) -> dict:
    return _redmine_request(
        method="POST",
        path=path,
        json_body=json_body,
    )

def redmine_put(
        path: str,
        json_body: dict | None = None,
) -> dict:
    return _redmine_request(
        method="PUT",
        path=path,
        json_body=json_body,
    )

def redmine_delete(
        path: str,
) -> dict:
    return _redmine_request(
        method="DELETE",
        path=path,
    )