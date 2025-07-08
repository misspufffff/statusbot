import os
import json
import requests
from flask import Flask, request, jsonify
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

# ─── Drive/Docs Helpers ──────────────────────────────────────────────────────
def find_doc_id_by_title(title):
    q = f"name = '{title}' and mimeType = 'application/vnd.google-apps.document'"
    resp = drive_service.files().list(q=q, fields="files(id)").execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None

def copy_project_doc(title):
    new = drive_service.files().copy(
        fileId=TEMPLATE_DOC_ID,
        body={"name": title}
    ).execute()
    return new["id"]

def append_update_to_doc(doc_id, cells):
    requests = [{
        "insertTableRow": {
            "tableCellLocation": {
                "tableStartLocation": {"index": 1},
                "rowIndex": 1
            },
            "insertBelow": True
        }
    }]
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

# ─── Flask App ────────────────────────────────────────────────────────────────
app = Flask(__name__)

# Caches the full list of Harvest project names in memory
HARVEST_PROJECTS = []

 def load_all_harvest_projects():
     """Fetch every Harvest project name, paging 100 at a time."""
     harvest_url = "https://api.harvestapp.com/v2/projects"
     headers = {
         "Harvest-Account-Id": os.environ["HARVEST_ACCOUNT_ID"],
         "Authorization":      f"Bearer {os.environ['HARVEST_ACCESS_TOKEN']}",
         "User-Agent":         "StatusBot (you@example.com)"
     }
     names = []
     page = 1
     while True:
         r = requests.get(
             harvest_url, headers=headers,
             params={"page": page, "per_page": 100}
         )
         r.raise_for_status()
         data = r.json()
-        names.extend(p["name"] for p in data.get("projects", []))
+        # only include active projects
+        names.extend(
+            p["name"]
+            for p in data.get("projects", [])
+            if p.get("is_active", False)
+        )
         if not data.get("next_page"):
             break
         page += 1
     return names

@app.before_request
def init_projects():
    load_all_harvest_projects()

@app.route("/slack/options", methods=["POST"])
def slack_options():
    """Provide dynamic project suggestions for external_select."""
    payload = json.loads(request.form["payload"])
    user_input = payload.get("value", "").lower()
    # filter project names by substring match
    matches = [p for p in HARVEST_PROJECTS if user_input in p.lower()]
    options = [{
        "text": {"type": "plain_text", "text": name},
        "value": name
    } for name in matches[:100]]  # Slack caps at 100
    return jsonify(options=options)

@app.route("/slack/command", methods=["POST"])
def slack_command():
    """Open modal with external_select for unlimited projects."""
    trigger_id = request.form["trigger_id"]
    channel_id = request.form["channel_id"]

    week_of = (datetime.now() - timedelta(days=datetime.now().weekday())) \
               .strftime("%B %d, %Y")

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
                        "type": "external_select",
                        "action_id": "project_select",
                        "min_query_length": 0,
                        "placeholder": {"type": "plain_text", "text": "Type to search…"}
                    }
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Week of {week_of}*"}
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
                        ("feedback", "Areas where we need customer feedback?"),
                        ("next_steps", "Next Steps:")
                    ]
                ]
            ]
        }
    )
    return "", 200

@app.route("/slack/interact", methods=["POST"])
def slack_interact():
    """Handle modal submission: update Doc + post to Slack."""
    payload = json.loads(request.form["payload"])
    if payload.get("type") != "view_submission":
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

    doc_id = find_doc_id_by_title(project_name) or copy_project_doc(project_name)
    append_update_to_doc(doc_id, [name, discipline, progress, challenges, feedback, next_steps])

    week_of = (datetime.now() - timedelta(days=datetime.now().weekday())) \
              .strftime("%B %d, %Y")
    slack_msg = (
        f"*Weekly Update – {project_name} (Week of {week_of})*\n"
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
