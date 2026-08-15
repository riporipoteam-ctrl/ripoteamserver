from __future__ import annotations

from typing import Any


def build_room_instance(
    *,
    account_id: int,
    room_id: int,
    location: str,
    photon_region: str,
    private: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the recovered old-client roomInstance DTO without web dependencies."""
    if isinstance(existing, dict) and int(existing.get("roomId") or -1) == int(room_id):
        return existing

    room_instance_id = int(account_id * 100_000 + (room_id % 100_000))
    return {
        "roomInstanceId": room_instance_id,
        "roomId": int(room_id),
        "subRoomId": 1,
        "location": location,
        "photonRegionId": photon_region,
        "photonRoomId": room_instance_id,
        "name": location,
        "maxCapacity": 4 if private else 8,
        "isFull": False,
        "isPrivate": bool(private),
        "isInProgress": False,
        "roomInstanceType": 0,
        "isMatchmakingSocial": False,
        "dataBlobName": "",
        "dataBlobChecksum": "",
        "dataBlob": None,
        "matchMakingPolicy": 0,
        "inviteCode": str(room_instance_id),
    }


def build_player(*, account_id: int, username: str, display_name: str, now_ms: int) -> dict[str, Any]:
    return {
        "playerId": int(account_id),
        "accountId": int(account_id),
        "username": username,
        "displayName": display_name,
        "statusVisibility": 1,
        "platform": "Steam",
        "isOnline": True,
        "lastHeartbeatAt": int(now_ms),
    }
