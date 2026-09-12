from __future__ import annotations

from typing import Any


# Recovered from Rec Room room config data used by multiple revival servers.
DORM_ROOM_REPLICATION_ID = "68251132-5662-5c34-08b1-4a830a27955b"
DORM_SCENE_REPLICATION_ID = "92084aee-1f44-a3b4-18f1-375601606506"
DORM_SCENE_LOCATION_ID = "76d98498-60a1-430c-ab76-b54a29b7a163"

# Recovered new-user Orientation flow. The client loads this room locally and
# uses the sentinel roomInstanceId -2; heartbeat has to echo it until onboarding
# transitions the player into a normal matched room.
ORIENTATION_ROOM_ID = 13
ORIENTATION_INSTANCE_ID = -2
ORIENTATION_SUBROOM_ID = 23
ORIENTATION_ROOM_REPLICATION_ID = "5a8e804a-d082-47cd-ab71-039aa680da14"
ORIENTATION_SCENE_REPLICATION_ID = "2c9da604-fdc8-47b7-b527-7a692ce97a9b"
ORIENTATION_SCENE_LOCATION_ID = "c79709d8-a31b-48aa-9eb8-cc31ba9505e8"


def _stable_instance_id(account_id: int, room_id: int) -> int:
    """Return an old-client-friendly positive 9-digit room instance id."""
    # Older revival implementations use values <= 999,999,999. Keep the id
    # deterministic for reconnect/heartbeat while staying well inside Int32.
    seed = ((int(account_id) & 0x7FFFFFFF) * 1103515245 + (int(room_id) & 0x7FFFFFFF) * 12345) & 0x7FFFFFFF
    return 100_000_000 + (seed % 900_000_000)


def build_orientation_instance(*, photon_region: str = "us") -> dict[str, Any]:
    """Build the special local Orientation presence used for a fresh account."""
    return {
        "roomInstanceId": ORIENTATION_INSTANCE_ID,
        "roomId": ORIENTATION_ROOM_ID,
        "subRoomId": ORIENTATION_SUBROOM_ID,
        "roomInstanceType": 0,
        "location": ORIENTATION_SCENE_LOCATION_ID,
        "dataBlob": None,
        "dataBlobName": "",
        "dataBlobChecksum": "",
        "eventId": 0,
        "clubId": 0,
        "roomCode": "",
        "photonRegion": str(photon_region),
        "photonRegionId": str(photon_region),
        "photonRoomId": "rec.13",
        "name": "^Orientation",
        "maxCapacity": 1,
        "isFull": False,
        "isPrivate": False,
        "isInProgress": False,
        "roomInstanceType": 0,
        "isMatchmakingSocial": False,
        "EncryptVoiceChat": False,
        "matchmakingPolicy": 0,
        "matchMakingPolicy": 0,
        "inviteCode": "",
    }


def build_room_instance(
    *,
    account_id: int,
    room_id: int,
    location: str,
    photon_region: str,
    private: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build/normalize the recovered old-client roomInstance DTO."""
    room_id = int(room_id)
    current: dict[str, Any] = dict(existing) if isinstance(existing, dict) and int(existing.get("roomId") or -1) == room_id else {}

    current_id = current.get("roomInstanceId")
    try:
        current_id = int(current_id)
    except (TypeError, ValueError):
        current_id = 0
    room_instance_id = current_id if 1 <= current_id <= 999_999_999 else _stable_instance_id(account_id, room_id)

    room_name = "DormRoom" if private else f"FluxRoom_{room_id}"
    photon_room_id = f"FluxRecRoom2022-{room_name}-1-{room_id}-{room_instance_id}"

    current.update(
        {
            "roomInstanceId": room_instance_id,
            "roomId": room_id,
            "subRoomId": 1,
            "location": str(location),
            "photonRegionId": str(photon_region),
            # Some client generations deserialize photonRegion separately.
            "photonRegion": str(photon_region),
            # Recovered revival servers use a string Photon room name, not the
            # numeric roomInstanceId itself.
            "photonRoomId": photon_room_id,
            "name": room_name,
            "maxCapacity": 4 if private else 8,
            "isFull": False,
            "isPrivate": bool(private),
            "isInProgress": False,
            "roomInstanceType": 0,
            "isMatchmakingSocial": False,
            "dataBlobName": "",
            "dataBlobChecksum": "",
            "dataBlob": None,
            # Keep both spellings because old server implementations expose
            # matchmakingPolicy while some reversed DTO notes use matchMakingPolicy.
            "matchmakingPolicy": 0,
            "matchMakingPolicy": 0,
            "inviteCode": str(room_instance_id),
        }
    )
    return current


def build_player(*, account_id: int, username: str, display_name: str, now_ms: int) -> dict[str, Any]:
    return {
        "playerId": int(account_id),
        "accountId": int(account_id),
        "username": username,
        "displayName": display_name,
        "statusVisibility": 3,
        # Older Matchmaking heartbeat DTOs use an enum integer here.
        "platform": -1,
        "deviceClass": 0,
        "vrMovementMode": 0,
        "isOnline": True,
        "lastOnline": None,
        "appVersion": "20220519",
        "lastHeartbeatAt": int(now_ms),
    }
