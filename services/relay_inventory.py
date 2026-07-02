import re


def _parse_channels_from_type(board_type):
    match = re.search(r"(\d+)ch", board_type or "")
    return int(match.group(1)) if match else 30


def get_relay_boards(config):
    """Return normalized list of relay boards with id, name, host, port, unit_id, channels, start_relay."""
    boards = config.get("relay_boards")
    if boards:
        result = []
        offset = 1
        for index, board in enumerate(boards):
            board_type = board.get("type", "")
            channels = int(board.get("channels") or _parse_channels_from_type(board_type))
            start = int(board.get("start_relay") or offset)
            result.append({
                "id": board.get("id", f"board{index + 1}"),
                "name": board.get("name", f"Módulo {index + 1}"),
                "type": board_type,
                "host": board.get("host", ""),
                "port": int(board.get("port", 502)),
                "unit_id": int(board.get("unit_id", 1)),
                "channels": channels,
                "start_relay": start,
            })
            offset = start + channels
        return result

    legacy = config.get("relay_board")
    if legacy:
        board_type = legacy.get("type", "")
        channels = _parse_channels_from_type(board_type)
        return [{
            "id": "board1",
            "name": legacy.get("name", "Módulo principal"),
            "type": board_type,
            "host": legacy.get("host", ""),
            "port": int(legacy.get("port", 502)),
            "unit_id": int(legacy.get("unit_id", 1)),
            "channels": channels,
            "start_relay": 1,
        }]
    return []


def get_relay_count(config):
    return sum(b.get("channels", 0) for b in get_relay_boards(config))


def get_board_for_relay(config, relay_number):
    for board in get_relay_boards(config):
        start = board.get("start_relay", 1)
        channels = board.get("channels", 0)
        if start <= relay_number < start + channels:
            return board, relay_number - start
    return None, None


def get_relay_name_map(config):
    return {
        entry["relay"]: entry.get("name", "")
        for entry in config.get("relay_map", [])
        if "relay" in entry
    }


def get_used_relays(config, exclude=None):
    exclude_ids = {id(obj) for obj in (exclude or [])}
    used = set()

    for tank in config.get("tanks", []):
        if id(tank) in exclude_ids:
            continue
        relays = tank.get("relays", {})
        for key in ("empty", "full"):
            value = relays.get(key, 0) or 0
            if value > 0:
                used.add(value)

    for source in config.get("sources", []):
        if id(source) in exclude_ids:
            continue
        for key in ("enable_relay", "valve_relay"):
            value = source.get(key, 0) or 0
            if value > 0:
                used.add(value)

    for route in config.get("routes", []):
        if id(route) in exclude_ids:
            continue
        value = route.get("valve_relay", 0) or 0
        if value > 0:
            used.add(value)

    return used


def get_available_relay_options(config, include=None, exclude=None):
    boards = get_relay_boards(config)
    used = get_used_relays(config, exclude=exclude)
    name_map = get_relay_name_map(config)

    include_set = {int(r) for r in (include or []) if r and int(r) > 0}

    options = []
    for board in boards:
        start = board.get("start_relay", 1)
        channels = board.get("channels", 0)
        for number in range(start, start + channels):
            if number in used and number not in include_set:
                continue
            options.append({
                "number": number,
                "name": name_map.get(number, ""),
                "board_id": board.get("id"),
                "board_name": board.get("name"),
            })

    return options

