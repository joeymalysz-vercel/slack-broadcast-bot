from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


def build_broadcast_blocks(
    title: str,
    body: str,
    category: str,
    sender_name: str,
    link: Optional[str],
) -> List[Dict[str, Any]]:
    ts = datetime.now(timezone.utc).strftime("%b %d, %Y • %H:%M UTC")
    header_text = f"{category}: {title}".strip(": ")

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text[:150]}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Sent by:* {sender_name}"},
                {"type": "mrkdwn", "text": f"*Time:* {ts}"},
            ],
        },
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]

    if link:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open link"},
                        "url": link,
                    }
                ],
            }
        )

    return blocks


def draft_modal_view(private_metadata: str) -> Dict[str, Any]:
    """
    Draft modal shown when user runs /partner_broadcast
    """
    return {
        "type": "modal",
        "callback_id": "broadcast_draft_submit",
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Partner Broadcast"},
        "submit": {"type": "plain_text", "text": "Review"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "title_block",
                "label": {"type": "plain_text", "text": "Title"},
                "element": {"type": "plain_text_input", "action_id": "title_input", "max_length": 120},
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "category_block",
                "label": {"type": "plain_text", "text": "Category"},
                "element": {
                    "type": "static_select",
                    "action_id": "category_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Release"}, "value": "Release"},
                        {"text": {"type": "plain_text", "text": "Incident"}, "value": "Incident"},
                        {"text": {"type": "plain_text", "text": "Action required"}, "value": "Action required"},
                        {"text": {"type": "plain_text", "text": "FYI"}, "value": "FYI"},
                    ],
                    "initial_option": {"text": {"type": "plain_text", "text": "Release"}, "value": "Release"},
                },
            },
            {
                "type": "input",
                "block_id": "body_block",
                "label": {"type": "plain_text", "text": "Message"},
                "element": {"type": "plain_text_input", "action_id": "body_input", "multiline": True},
            },
            {
                "type": "input",
                "block_id": "link_block",
                "label": {"type": "plain_text", "text": "Optional link"},
                "element": {"type": "plain_text_input", "action_id": "link_input"},
                "optional": True,
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "Next: you’ll review exactly what partners will see before sending."}
                ],
            },
        ],
    }


def review_modal_view(private_metadata: str, preview_blocks: List[Dict[str, Any]], channel_count: int) -> Dict[str, Any]:
    """
    Review modal shown after Draft is submitted; includes Edit + Send buttons.
    """
    return {
        "type": "modal",
        "callback_id": "broadcast_review",
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Review Broadcast"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Ready to send to* *{channel_count}* *channel(s).*"},
            },
            {"type": "divider"},
            *preview_blocks,
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "edit_draft",
                        "text": {"type": "plain_text", "text": "Edit"},
                        "style": "secondary",
                    },
                    {
                        "type": "button",
                        "action_id": "send_broadcast",
                        "text": {"type": "plain_text", "text": "Send"},
                        "style": "primary",
                    },
                ],
            },
        ],
    }
