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


def get_relay_assignments(config):
    """Return one row per configured relay describing what (if anything)
    it is currently assigned to.

    Row shape:
        {
            "number": int,
            "board_id": str,
            "board_name": str,
            "assignments": [
                {"kind": "tank_empty" | "tank_full"
                          | "source_enable" | "route",
                 "label": str,
                 "source_id": str | None,
                 "tank_id": str | None}
            ]  # may be empty when the relay is free
        }
    """
    # Build a lookup from relay number -> list of assignments.
    assignments_by_relay = {}

    def _push(relay_number, entry):
        if not relay_number or relay_number <= 0:
            return
        assignments_by_relay.setdefault(int(relay_number), []).append(entry)

    for tank in config.get("tanks", []):
        tank_id = tank.get("id")
        tank_name = tank.get("name", tank_id)
        relays = tank.get("relays", {}) or {}
        empty_relay = relays.get("empty", 0) or 0
        full_relay = relays.get("full", 0) or 0
        if empty_relay:
            _push(empty_relay, {
                "kind": "tank_empty",
                "label": f"Tanque {tank_name} — sensor de vazio",
                "source_id": None,
                "tank_id": tank_id,
            })
        if full_relay:
            _push(full_relay, {
                "kind": "tank_full",
                "label": f"Tanque {tank_name} — sensor de cheio",
                "source_id": None,
                "tank_id": tank_id,
            })

    tank_names = {t.get("id"): t.get("name", t.get("id")) for t in config.get("tanks", [])}
    source_names = {s.get("id"): s.get("name", s.get("id")) for s in config.get("sources", [])}

    for source in config.get("sources", []):
        source_id = source.get("id")
        source_name = source_names.get(source_id, source_id)
        enable_relay = source.get("enable_relay", source.get("valve_relay", 0)) or 0
        if enable_relay:
            _push(enable_relay, {
                "kind": "source_enable",
                "label": f"Fonte {source_name} — relé do equipamento",
                "source_id": source_id,
                "tank_id": None,
            })

    for route in config.get("routes", []):
        source_id = route.get("source_id")
        tank_id = route.get("tank_id")
        valve_relay = route.get("valve_relay", 0) or 0
        if valve_relay:
            source_name = source_names.get(source_id, source_id)
            tank_name = tank_names.get(tank_id, tank_id)
            _push(valve_relay, {
                "kind": "route",
                "label": f"Rota {source_name} → {tank_name}",
                "source_id": source_id,
                "tank_id": tank_id,
            })

    # Emit one row per configured relay slot on every board.
    rows = []
    for board in get_relay_boards(config):
        start = board.get("start_relay", 1)
        channels = board.get("channels", 0)
        for number in range(start, start + channels):
            rows.append({
                "number": number,
                "board_id": board.get("id"),
                "board_name": board.get("name"),
                "assignments": assignments_by_relay.get(number, []),
            })

    return rows

