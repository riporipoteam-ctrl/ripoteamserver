from __future__ import annotations

import recroom_nameserver_fix as nameserver_fix


_CONTRACT_REVISION = "aug25-2021-nameserver-v1"

_SERVICE_SUFFIX_2021: dict[str, str] = {
    "RecNetStatus": "",
    "Auth": "",
    "API": "",
    "WWW": "",
    "Notifications": "/no",
    "Images": "",
    "CDN": "",
    "Commerce": "/shop",
    "Matchmaking": "/m",
    "Storage": "",
    "Chat": "",
    "Leaderboard": "/leaderb",
    "Accounts": "/acct",
    "Link": "",
    "RoomComments": "",
    "Clubs": "/c",
    "Rooms": "/r",
    "PlatformNotifications": "/no",
    "Moderation": "",
    "DataCollection": "",
    "BugReporting": "",
    "Discovery": "/disco",
    "Econ": "",
    "CMS": "",
    "GameLogs": "",
    "Lists": "/l",
    "PlayerSettings": "/psettingsx",
    "Strings": "",
    "StringsCDN": "",
    "Studio": "",
}


def _nameserver_payload_2021(local_base: str) -> dict[str, str]:
    return {field: f"{local_base}{suffix}" for field, suffix in _SERVICE_SUFFIX_2021.items()}


nameserver_fix._SERVICE_SUFFIX = dict(_SERVICE_SUFFIX_2021)
nameserver_fix._nameserver_payload = _nameserver_payload_2021
setattr(nameserver_fix, "_CONTRACT_REVISION", _CONTRACT_REVISION)
print(f"Rec Room 2021 nameserver contract loaded: {_CONTRACT_REVISION}")
