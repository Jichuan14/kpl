from typing import Any


def player_display_name(node: dict[str, Any]) -> str:
    return str(
        node.get("actual_player_name") or node.get("player_name") or ""
    ).strip()


def api_camp_for_team(data: dict[str, Any], team_id: str) -> int | None:
    if not team_id:
        return None
    for camp, key in ((1, "camp1"), (2, "camp2")):
        node = data.get(key) or {}
        if str(node.get("team_id") or "") == team_id:
            return camp
    for player in data.get("battle_player_list") or []:
        if not isinstance(player, dict):
            continue
        if str(player.get("team_id") or "") == team_id:
            camp = int(player.get("camp") or 0)
            return camp if camp in (1, 2) else None
    return None


def compute_camp_flip(data: dict[str, Any], match_camp1_team_id: str) -> int:
    api_camp = api_camp_for_team(data, match_camp1_team_id)
    return 1 if api_camp == 2 else 0


def to_match_camp(api_camp: int, camp_flip: int) -> int:
    if api_camp not in (1, 2) or camp_flip != 1:
        return api_camp
    return 2 if api_camp == 1 else 1
