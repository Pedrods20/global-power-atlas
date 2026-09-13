"""Configure the authorized portfolio repository using local Git credentials.

No credential is printed or stored here. Requires httpx.
"""

import argparse
import os
import subprocess

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "pages", "status", "runs"])
    args = parser.parse_args()
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True, capture_output=True, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    credential = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    client = httpx.Client(base_url="https://api.github.com", timeout=40, headers={
        "Authorization": "Bearer " + credential["password"],
        "Accept": "application/vnd.github+json",
    })
    profile = client.get("/user").json()
    owner = profile["login"]
    repo = f"/repos/{owner}/global-power-atlas"
    if args.action == "create":
        existing = client.get(repo)
        if existing.status_code == 404:
            response = client.post("/user/repos", json={
                "name": "global-power-atlas", "private": False,
                "description": "Reproducible electricity supply, demand and price analytics across global markets.",
                "homepage": f"https://{owner.lower()}.github.io/global-power-atlas/",
                "has_issues": True,
            })
            response.raise_for_status()
        print(f"https://github.com/{owner}/global-power-atlas")
    elif args.action == "pages":
        exists = client.get(repo + "/pages")
        response = client.request("PUT" if exists.is_success else "POST", repo + "/pages", json={"build_type": "workflow"})
        response.raise_for_status()
        print("Pages configured for GitHub Actions")
    elif args.action == "runs":
        response = client.get(repo + "/actions/runs", params={"per_page": 10})
        response.raise_for_status()
        for run in response.json()["workflow_runs"]:
            print(run["id"], run["name"], run["status"], run["conclusion"], run["head_sha"][:8], run["html_url"])
    else:
        for suffix in ("", "/pages"):
            response = client.get(repo + suffix)
            print(suffix or "repository", response.status_code, response.json().get("html_url"))


if __name__ == "__main__":
    main()
