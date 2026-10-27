"""JSON schemas for the napcat toolset.

Handlers must return JSON strings (see tools.py); these schemas are what the
LLM sees.
"""

TARGET_DESC = (
    "Target chat. Use a plain QQ number for a private chat, or 'g' + group id "
    "(e.g. 'g987654321') for a group. The chat ids shown in the conversation "
    "source can be used verbatim."
)

SEND_MESSAGE = {
    "name": "napcat_send_message",
    "description": (
        "Proactively send a QQ message to a private chat or group via NapCat. "
        "Use this to push results or notifications to chats outside the "
        "current conversation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": TARGET_DESC},
            "message": {"type": "string", "description": "Message text to send."},
        },
        "required": ["target", "message"],
    },
}

SEND_FILE = {
    "name": "napcat_send_file",
    "description": (
        "Send a local file to a QQ private chat or group via NapCat. The path "
        "must be readable by NapCat (when NapCat runs in another container, "
        "use a path on a shared volume)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": TARGET_DESC},
            "file_path": {"type": "string", "description": "Absolute path of the file to send."},
            "file_name": {
                "type": "string",
                "description": "Optional display name for the uploaded file.",
            },
        },
        "required": ["target", "file_path"],
    },
}

GET_GROUP_LIST = {
    "name": "napcat_get_group_list",
    "description": "List the QQ groups the bot account has joined.",
    "parameters": {"type": "object", "properties": {}},
}

GET_GROUP_HISTORY = {
    "name": "napcat_get_group_history",
    "description": (
        "Fetch recent chat history of a QQ group on demand. Use this when the "
        "user asks what the group has been talking about - the bot does NOT "
        "passively read group chat, it only pulls history when asked."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
            "count": {
                "type": "integer",
                "description": "Number of recent messages to fetch (default 20, max 100).",
            },
        },
        "required": ["group_id"],
    },
}

GET_FORWARD_MSG = {
    "name": "napcat_get_forward_msg",
    "description": (
        "Unpack a merged-forward QQ message (合并转发消息) into its individual "
        "floors. Pass the forward message's message_id. The bot shows merged "
        "forwards as '[合并转发消息]' - use this tool to read what is inside."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message id of the merged-forward message.",
            },
        },
        "required": ["message_id"],
    },
}

GET_GROUP_MEMBER_INFO = {
    "name": "napcat_get_group_member_info",
    "description": "Get a member's info (nickname, card, role, title) in a QQ group.",
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
            "user_id": {"type": "integer", "description": "Member QQ number."},
        },
        "required": ["group_id", "user_id"],
    },
}

GROUP_MUTE = {
    "name": "napcat_group_mute",
    "description": (
        "Mute (ban) a member in a QQ group for a duration. Use duration 0 to "
        "lift a mute. Requires the bot to be a group admin. This is a "
        "moderation action against a real user - be sure before calling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
            "user_id": {"type": "integer", "description": "Member QQ number."},
            "duration_seconds": {
                "type": "integer",
                "description": "Mute duration in seconds (0 to unmute, max 2592000).",
            },
        },
        "required": ["group_id", "user_id", "duration_seconds"],
    },
}

GROUP_KICK = {
    "name": "napcat_group_kick",
    "description": (
        "Remove a member from a QQ group. Requires the bot to be a group "
        "admin. Destructive and irreversible - only use when explicitly "
        "requested by an authorized user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
            "user_id": {"type": "integer", "description": "Member QQ number."},
            "reject_add_request": {
                "type": "boolean",
                "description": "Also block the member from re-joining.",
            },
        },
        "required": ["group_id", "user_id"],
    },
}

ALL = [
    SEND_MESSAGE,
    SEND_FILE,
    GET_GROUP_LIST,
    GET_GROUP_HISTORY,
    GET_FORWARD_MSG,
    GET_GROUP_MEMBER_INFO,
    GROUP_MUTE,
    GROUP_KICK,
]
