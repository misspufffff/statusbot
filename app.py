import os
import json
import requests
from flask import Flask, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ─── Slack Client ─────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
slack = WebClient(token=SLACK_BOT_TOKEN)

# ─── Google API Clients ───────────────────────────────────────────────────────
with open("service-account.json") as f:
    sa_info = json.load(f)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]
creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
drive_service = build("drive", "v3", credentials=creds)
docs_service  = build("docs", "v1", credentials=creds)

TEMPLATE_DOC_ID = os.environ["TEMPLATE_DOC_ID"]
# (Optional) PARENT_FOLDER_ID = os.environ.get("PARENT_FOLDER_ID")

# ─── Drive/Docs Helper Functions ─────────────────────────────────────────────
def find_doc_id_by_title(title):
    q = f"name = '{title}' and mimeType = 'application/vnd.google-apps.document'"
    resp = drive_service.files().list(q=q, fields="files(id)").execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None

def copy_project_doc(title):
    body = {"name": title}
    # body["parents"] = [PARENT_FOLDER_ID]  # if you want a folder
    new = drive_service.files().copy(fileId=TEMPLATE_DOC_ID, body=body).execute()
    return new["id"]

def append_update_to_doc(doc_id, cells):
    requests = [
        {
            "insertTableRow": {
                "tableCellLocation": {
                    "tableStartLocation": {"index": 1},
                    "rowIndex": 1
                },
                "insertBelow": True
            }
        }
    ]
    for text in cells:
        requests.append({
            "insertText": {
                "location": {"index": None, "segmentId": ""},
                "text": text
            }
        })
    docs_service.documents().batchUpdate(
        documentId=doc_id, body={"requests": requests}
    ).execute()

# ─── Flask App & Slack Endpoints ──────────────────────────────────────────────
app = Flask(__name__)

@app.route("/slack/command", methods=["POST"])
def slack_command():
    """Slash command: /weekly-update → open a modal."""
    trigger_id = request.form["trigger_id"]
    channel_id = request.form["channel_id"]

    # Fetch Harvest projects
    harvest_url = "https://api.harvestapp.com/v2/projects"
    headers = {
        "Harvest-Account-Id": os.environ["HARVEST_ACCOUNT_ID"],
        "Authorization":      f"Bearer {os.environ['HARVEST_ACCESS_TOKEN']}",
        "User-Agent":         "StatusBot (mclaypoole@kickrdesign.com)"
    }
    resp = requests.get(harvest_url, headers=headers)
    resp.raise_for_status()
    projects = resp.json().get("projects", [])

    project_options = [
        {
            "text":  {"type": "plain_text", "text": proj["name"]},
            "value": proj["name"]
        }
        for proj in projects
    ]

    slack.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "weekly_update",
            "private_metadata": channel_id,
            "title": {"type": "plain_text", "text": "Weekly Update"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "project",
                    "label": {"type": "plain_text", "text": "Project"},
                    "element": {
                        "type": "static_select",
                        "action_id": "project_select",
                        "options": project_options
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Week of {(datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%B %d, %Y')}*"
                    }
                },
                {
                    "type": "input",
                    "block_id": "name",
                    "label": {"type": "plain_text", "text": "Your Name"},
                    "element": {"type": "plain_text_input", "action_id": "name_input"}
                },
                {
                    "type": "input",
                    "block_id": "discipline",
                    "label": {"type": "plain_text", "text": "Discipline (ID/ME/EE)"},
                    "element": {
                        "type": "static_select",
                        "action_id": "discipline_input",
                        "options": [
                            {"text": {"type": "plain_text", "text": "ID"}, "value": "ID"},
                            {"text": {"type": "plain_text", "text": "ME"}, "value": "ME"},
                            {"text": {"type": "plain_text", "text": "EE"}, "value": "EE"},
                        ]
                    }
                },
                *[
                    {
                        "type": "input",
                        "block_id": blk,
                        "label": {"type": "plain_text", "text": txt},
                        "element": {"type": "plain_text_input", "multiline": True, "action_id": f"{blk}_input"}
                    }
                    for blk, txt in [
                        ("progress", "What was worked on, what progress was made?"),
                        ("challenges", "Challenges, unexpected items, or timing issues?"),
                        ("feedback", "Are there any areas where we need feedback from the customer?"),
                        ("next_steps", "Next Steps:")
                    ]
                ]
            ]
        }
    )
    return "", 200

@app.route("/slack/interact", methods=["POST"])
def slack_interact():
    """Handle modal submission: write to Doc + post to Slack."""
    payload = json.loads(request.form["payload"])
    if payload["type"] != "view_submission":
        return "", 200

    vals = payload["view"]["state"]["values"]
    channel_id   = payload["view"]["private_metadata"]
    project_name = vals["project"]["project_select"]["selected_option"]["value"]
    name         = vals["name"]["name_input"]["value"]
    discipline   = vals["discipline"]["discipline_input"]["selected_option"]["value"]
    progress     = vals["progress"]["progress_input"]["value"]
    challenges   = vals["challenges"]["challenges_input"]["value"]
    feedback     = vals["feedback"]["feedback_input"]["value"]
    next_steps   = vals["next_steps"]["next_steps_input"]["value"]

    doc_id = find_doc_id_by_title(project_name)
    if not doc_id:
        doc_id = copy_project_doc(project_name)

    append_update_to_doc(doc_id, [name, discipline, progress, challenges, feedback, next_steps])

    monday_str = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("Week of %B %d, %Y")
    slack_msg = (
        f"*Weekly Update – {project_name} ({monday_str})*\n"
        f"> *Name:* {name}\n"
        f"> *Discipline:* {discipline}\n"
        f"> *Progress:* {progress}\n"
        f"> *Challenges:* {challenges}\n"
        f"> *Feedback:* {feedback}\n"
        f"> *Next Steps:* {next_steps}"
    )
    try:
        slack.chat_postMessage(channel=channel_id, text=slack_msg)
    except SlackApiError as e:
        print("Slack post error:", e.response["error"])

    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
