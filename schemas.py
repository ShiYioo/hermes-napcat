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

ADD_STICKER = {
    "name": "napcat_add_sticker",
    "description": (
        "Add an image to the bot account's QQ favorite stickers (自定义表情). "
        "Pass either 'url' (an image URL reachable by NapCat, e.g. a QQ CDN "
        "multimedia link) or 'message_id' (a message containing an image). "
        "Optional 'name' sets the sticker file name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Image URL (QQ CDN multimedia links work)."}, 
            "message_id": {"type": "string", "description": "Message id carrying the image (alternative to url)."},
            "name": {"type": "string", "description": "Optional sticker file name (extension auto-appended)."},
        },
        "required": [],
    },
}

LIST_STICKERS = {
    "name": "napcat_list_stickers",
    "description": "List the bot account's QQ favorite stickers (自定义表情), with res_id/md5/path/url per item.",
    "parameters": {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "Max stickers to fetch (default 48)."},
        },
        "required": [],
    },
}

REMOVE_STICKER = {
    "name": "napcat_remove_sticker",
    "description": "Remove a sticker from the bot account's QQ favorite stickers by res_id (from napcat_list_stickers).",
    "parameters": {
        "type": "object",
        "properties": {
            "res_id": {"type": "string", "description": "Sticker res_id (from napcat_list_stickers)."},
        },
        "required": ["res_id"],
    },
}

POKE = {
    "name": "napcat_send_poke",
    "description": (
        "Poke (戳一戳) a user. Pass user_id for a private poke, or user_id + "
        "group_id for a group poke. Playful attention-getter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "QQ number to poke."},
            "group_id": {"type": "integer", "description": "Group id (omit for private poke)."},
        },
        "required": ["user_id"],
    },
}

GET_ESSENCE_MSGS = {
    "name": "napcat_get_essence_msgs",
    "description": "List a group's essence messages (精华消息) - the curated best-of set by group admins.",
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
        },
        "required": ["group_id"],
    },
}

GROUP_SIGN = {
    "name": "napcat_group_sign",
    "description": "Sign in (打卡) to a group on behalf of the bot account. Requires the account to have signing available.",
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
        },
        "required": ["group_id"],
    },
}

SET_INPUT_STATUS = {
    "name": "napcat_set_input_status",
    "description": "Show or clear the 'typing...' indicator toward a user (human-like touch before long replies).",
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "QQ number to show typing status to."},
            "typing": {"type": "integer", "description": "1 = typing (default), 0 = clear."},
        },
        "required": ["user_id"],
    },
}

GET_PRIVATE_HISTORY = {
    "name": "napcat_get_private_history",
    "description": (
        "Fetch recent private-chat history with a user (the DM counterpart of "
        "napcat_get_group_history). Use for reviewing past conversations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "QQ number."},
            "count": {"type": "integer", "description": "Number of recent messages (default 20, max 100)."},
        },
        "required": ["user_id"],
    },
}

SEND_LIKE = {
    "name": "napcat_send_like",
    "description": "Give a user's profile a like (点赞). Rate-limited by QQ: max 10 per user per day, 1-10 in one call.",
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "QQ number to like."},
            "times": {"type": "integer", "description": "Like count 1-10 (default 1)."},
        },
        "required": ["user_id"],
    },
}

CHECK_URL_SAFETY = {
    "name": "napcat_check_url_safety",
    "description": (
        "Check whether a URL is flagged unsafe by QQ's safety service before "
        "opening or sharing it. Verdict: safe / unknown / dangerous."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to check."},
        },
        "required": ["url"],
    },
}

LIST_GROUP_FILES = {
    "name": "napcat_list_group_files",
    "description": "List a group's shared files (root listing, or a subfolder via folder_id).",
    "parameters": {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "Group id."},
            "folder_id": {"type": "string", "description": "Folder id (omit for root)."},
        },
        "required": ["group_id"],
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
    ADD_STICKER,
    LIST_STICKERS,
    REMOVE_STICKER,
    POKE,
    GET_ESSENCE_MSGS,
    GROUP_SIGN,
    SET_INPUT_STATUS,
    GET_PRIVATE_HISTORY,
    SEND_LIKE,
    CHECK_URL_SAFETY,
    LIST_GROUP_FILES,
    GROUP_MUTE,
    GROUP_KICK,
]
