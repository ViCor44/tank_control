from pymodbus.client import ModbusTcpClient


def relay_index_to_address(relay_number):
    if relay_number < 1 or relay_number > 30:
        raise ValueError(f"Relay inválido: {relay_number}. Esperado 1..30")
    return relay_number - 1


class RelayBoardService:
    def __init__(self, host, port=502, unit_id=1, timeout=2):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout

    def _client(self):
        return ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
        )

    def set_relay(self, relay_number, on):
        address = relay_index_to_address(relay_number)
        client = self._client()

        try:
            if not client.connect():
                return {
                    "ok": False,
                    "error": f"Não foi possível ligar ao módulo de relés {self.host}:{self.port}"
                }

            result = client.write_coil(
                address=address,
                value=bool(on),
                slave=self.unit_id
            )

            if result.isError():
                return {
                    "ok": False,
                    "error": f"Erro Modbus ao escrever no relé {relay_number}: {result}"
                }

            return {
                "ok": True,
                "relay": relay_number,
                "state": bool(on)
            }
        finally:
            client.close()

    def relay_on(self, relay_number):
        return self.set_relay(relay_number, True)

    def relay_off(self, relay_number):
        return self.set_relay(relay_number, False)

    def read_relays(self, start_relay=1, count=30):
        if start_relay < 1 or start_relay > 30:
            raise ValueError("start_relay deve estar entre 1 e 30")
        if count < 1 or start_relay + count - 1 > 30:
            raise ValueError("Intervalo de leitura inválido")

        address = relay_index_to_address(start_relay)
        client = self._client()

        try:
            if not client.connect():
                return {
                    "ok": False,
                    "error": f"Não foi possível ligar ao módulo de relés {self.host}:{self.port}"
                }

            result = client.read_coils(
                address=address,
                count=count,
                slave=self.unit_id
            )

            if result.isError():
                return {
                    "ok": False,
                    "error": f"Erro Modbus ao ler relés: {result}"
                }

            states = []
            for index, bit in enumerate(result.bits[:count], start=start_relay):
                states.append({
                    "relay": index,
                    "state": bool(bit)
                })

            return {
                "ok": True,
                "states": states
            }
        finally:
            client.close()

    def all_off(self):
        client = self._client()

        try:
            if not client.connect():
                return {
                    "ok": False,
                    "error": f"Não foi possível ligar ao módulo de relés {self.host}:{self.port}"
                }

            result = client.write_coils(
                address=0,
                values=[False] * 30,
                slave=self.unit_id
            )

            if result.isError():
                return {
                    "ok": False,
                    "error": f"Erro Modbus ao desligar todos os relés: {result}"
                }

            return {
                "ok": True
            }
        finally:
            client.close()


def build_relay_board_service(config):
    relay_board = config.get("relay_board", {})
    return RelayBoardService(
        host=relay_board.get("host", "192.168.63.140"),
        port=relay_board.get("port", 502),
        unit_id=relay_board.get("unit_id", 1),
        timeout=config.get("system", {}).get("sensor_request_timeout_seconds", 2),
    )