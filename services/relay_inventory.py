import re


def get_relay_count(config):
    board_type = config.get("relay_board", {}).get("type", "")
    match = re.search(r"(\d+)ch", board_type)
    if match:
        return int(match.group(1))
    return 30


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
    total = get_relay_count(config)
    used = get_used_relays(config, exclude=exclude)
    name_map = get_relay_name_map(config)

    include_set = {int(r) for r in (include or []) if r and int(r) > 0}

    options = []
    for number in range(1, total + 1):
        if number in used and number not in include_set:
            continue
        options.append({
            "number": number,
            "name": name_map.get(number, ""),
        })

    return options
