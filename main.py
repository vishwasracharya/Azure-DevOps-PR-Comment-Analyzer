import requests
from base64 import b64encode
from urllib.parse import unquote
import logging
import time
import os
import re
import argparse
from collections import defaultdict
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

# ---------------- CONFIG ----------------
ORGANIZATION = "YOUR_ORG_NAME"
PROJECT = "YOUR_PROJECT_NAME"
API_VERSION = "7.1"

# PAT via env var only (public-safe)
PAT = os.getenv("AZURE_DEVOPS_PAT")
# --------------------------------------

# ---------------- LOGGING ----------------
logging.basicConfig(
    filename="pr_comment_analyzer.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_auth_header(pat: str):
    token = ":" + pat
    encoded = b64encode(token.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}"}


def safe_request(method, url, headers=None, params=None, json=None,
                 max_retries=3, backoff_base=2):
    headers = headers or {}
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(method, url, headers=headers,
                                     params=params, json=json, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt < max_retries:
                time.sleep(backoff_base ** attempt)
                continue
            raise


# ---------------- FILTERING ----------------
STATUS_PATTERNS = [
    r"policy status has been updated",
    r"voted",
    r"updated the pull request status to",
    r"joined as a reviewer",
    r"Conflicts are resolved",
    r"Submitted conflict resolution",
    r"from the reviewers",
    r"a required reviewer",
    r"an optional reviewer",
    r"as a reviewer",
    r"set auto-complete",
    r"is changed to be a required reviewer",
    r"SonarQube",
    r"voted\s+\d+",
    r"the reference refs/heads/.*was updated",
    r"updated the pull request status to Abandoned",
    r"\b(merged|abandoned|completed)\b",
]
STATUS_REGEX = re.compile("|".join(STATUS_PATTERNS), re.IGNORECASE)
SYSTEM_ACTOR = "microsoft.visualstudio.services.tfs"


def is_noise_comment(text: str, author_unique: str) -> bool:
    if not text or len(text.strip()) < 4:
        return True
    if author_unique.lower().startswith(SYSTEM_ACTOR):
        return True
    if STATUS_REGEX.search(text):
        return True
    return False


def classify_team(author: str, team_a: set, team_b: set) -> str:
    if author in team_a:
        return "team_a"
    if author in team_b:
        return "team_b"
    return "other"


# ---------------- AZURE DEVOPS ----------------
def get_linked_prs(work_item_id: int, headers):
    url = (
        f"https://dev.azure.com/{ORGANIZATION}/{PROJECT}"
        f"/_apis/wit/workitems/{work_item_id}"
        f"?$expand=relations&api-version={API_VERSION}"
    )
    res = safe_request("GET", url, headers=headers).json()
    prs = []

    for rel in res.get("relations", []):
        if rel.get("attributes", {}).get("name") == "Pull Request":
            decoded = unquote(rel["url"].split("/")[-1])
            parts = decoded.split("/")
            if len(parts) >= 2:
                prs.append((parts[-2], parts[-1]))
    return prs


def fetch_threads(repo_id: str, pr_id: str, headers):
    url = (
        f"https://dev.azure.com/{ORGANIZATION}/{PROJECT}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/threads"
        f"?api-version={API_VERSION}"
    )
    return safe_request("GET", url, headers=headers).json().get("value", [])


# ---------------- MAIN ----------------
def parse_args():
    parser = argparse.ArgumentParser("Azure DevOps PR Comment Analyzer")
    parser.add_argument("--tickets", nargs="+", type=int, required=True)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    if not PAT:
        raise RuntimeError("AZURE_DEVOPS_PAT environment variable not set")

    headers = get_auth_header(PAT)
    headers["Content-Type"] = "application/json"

    # Placeholder teams (safe for public repo)
    team_a = {"user1@example.com"}
    team_b = {"user2@example.com"}

    rows = []
    debug_stats = defaultdict(int)

    for wid in args.tickets:
        prs = get_linked_prs(wid, headers)
        debug_stats["tickets_processed"] += 1

        for repo_id, pr_id in prs:
            threads = fetch_threads(repo_id, pr_id, headers)

            for thread in threads:
                for comment in thread.get("comments", []):
                    debug_stats["comments_seen"] += 1

                    author = (comment.get("author", {})
                              .get("uniqueName", "")).lower()
                    text = comment.get("content", "")

                    if is_noise_comment(text, author):
                        debug_stats["comments_filtered"] += 1
                        continue

                    team = classify_team(author, team_a, team_b)
                    debug_stats["comments_kept"] += 1

                    rows.append({
                        "ticket_id": wid,
                        "repo_id": repo_id,
                        "pr_id": pr_id,
                        "author": author,
                        "team": team,
                        "comment": text,
                        "created_date": comment.get("createdDate"),
                    })

    if args.debug:
        print("\n🐞 DEBUG STATS")
        for k, v in debug_stats.items():
            print(f"{k:25}: {v}")

    if not rows:
        print("No meaningful comments found.")
        return

    df = pd.DataFrame(rows)

    # ---------------- EXCEL OUTPUT ----------------
    output_dir = Path(__file__).parent
    excel_path = output_dir / "pr_comment_report.xlsx"

    summary = (
        df.groupby(["team"])
        .size()
        .reset_index(name="comment_count")
    )

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Detailed Comments", index=False)
        summary.to_excel(writer, sheet_name="Team Summary", index=False)

    # ---------------- CHARTS ----------------
    pie_path = output_dir / "comments_by_team_pie.png"
    summary.set_index("team")["comment_count"].plot(
        kind="pie", autopct="%1.1f%%", title="Comments by Team"
    )
    plt.ylabel("")
    plt.tight_layout()
    plt.savefig(pie_path)
    plt.close()

    bar_path = output_dir / "comments_by_team_bar.png"
    summary.plot(kind="bar", x="team", y="comment_count",
                 legend=False, title="Comments by Team")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(bar_path)
    plt.close()

    print(f"Excel report generated: {excel_path}")
    print(f"Charts generated: {pie_path}, {bar_path}")


if __name__ == "__main__":
    main()
