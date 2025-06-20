from flask import Flask, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta
import os
import json

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
client = WebClient(token=SLACK_BOT_TOKEN)

app = Flask(__name__)

@app.route("/slack/command", methods=["POST"])
def slack_command():
    try:
        trigger_id = request.form.get("trigger_id")
        channel_id = request.form.get("channel_id")

        # Get current week's Monday
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        monday_str = monday.strftime("Week of %B %d, %Y")

        # Open modal
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "weekly_update",
                "private_metadata": channel_id,
                "title": {"type": "plain_text", "text": f"Weekly Update"},
                "submit": {"type": "plain_text", "text": "Submit"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Weekly Update – {monday_str}*"},
                    },
                    {
                        "type": "input",
                        "block_id": "name",
                        "label": {"type": "plain_text", "text": "Your Name"},
                        "element": {"type": "plain_text_input", "action_id": "name_input"},
                    },
                    {
                        "type": "input",
                        "block_id": "discipline",
                        "label": {"type": "plain_text", "text": "Choose your area:"},
                        "element": {
                            "type": "static_select",
                            "action_id": "discipline_input",
                            "options": [
                                {"text": {"type": "plain_text", "text": "ID"}, "value": "id"},
                                {"text": {"type": "plain_text", "text": "ME"}, "value": "me"},
                                {"text": {"type": "plain_text", "text": "EE"}, "value": "ee"},
                            ],
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "progress",
                        "label": {"type": "plain_text", "text": "What was worked on, what progress was made?"},
                        "element": {"type": "plain_text_input", "multiline": True, "action_id": "progress_input"},
                    },
                    {
                        "type": "input",
                        "block_id": "challenges",
                        "label": {"type": "plain_text", "text": "Challenges or timing surprises?"},
                        "element": {"type": "plain_text_input", "multiline": True, "action_id": "challenges_input"},
                    },
                    {
                        "type": "input",
                        "block_id": "feedback",
                        "label": {"type": "plain_text", "text": "Need feedback from customer?"},
                        "element": {"type": "plain_text_input", "multiline": True, "action_id": "feedback_input"},
                    },
                    {
                        "type": "input",
                        "block_id": "next_steps",
                        "label": {"type": "plain_text", "text": "Next Steps"},
                        "element": {"type": "plain_text_input", "multiline": True, "action_id": "steps_input"},
                    },
                ],
            },
        )
        return "", 200

    except Exception as e:
        print("❌ Error in slash_command:", str(e))
        return "", 200

@app.route("/slack/interact", methods=["POST"])
def slack_interact():
    try:
        payload = json.loads(request.form["payload"])
        values = payload["view"]["state"]["values"]
        channel_id = payload["view"]["private_metadata"]

        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        monday_str = monday.strftime("Week of %B %d, %Y")

        name = values["name"]["name_input"]["value"]
        discipline = values["discipline"]["discipline_input"]["selected_option"]["text"]["text"]
        progress = values["progress"]["progress_input"]["value"]
        challenges = values["challenges"]["challenges_input"]["value"]
        feedback = values["feedback"]["feedback_input"]["value"]
        next_steps = values["next_steps"]["steps_input"]["value"]

        message = (
            f"*Weekly Update – {discipline} ({monday_str})*\n"
            f"> *Name:* {name}\n"
            f"> *Progress:* {progress}\n"
            f"> *Challenges:* {challenges}\n"
            f"> *Feedback Needed:* {feedback}\n"
            f"> *Next Steps:* {next_steps}"
        )

        client.chat_postMessage(channel=channel_id, text=message)
        return "", 200

    except Exception as e:
        print("❌ Error in slack_interact:", str(e))
        return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
