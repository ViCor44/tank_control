from pymodbus.client import ModbusTcpClient

from services.relay_inventory import get_board_for_relay, get_relay_boards


class RelayBoardService:
    def __init__(self, host, port=502, unit_id=1, channels=30, timeout=2):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.channels = channels
        self.timeout = timeout

    def _channel_to_address(self, channel_number):
        if channel_number < 1 or channel_number > self.channels:
            raise ValueError(f"Canal inválido: {channel_number}. Esperado 1..{self.channels}")
        return channel_number - 1

    def _client(self):
        return ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
        )

    def set_channel(self, channel_number, on):
        address = self._channel_to_address(channel_number)
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
                    "error": f"Erro Modbus ao escrever no canal {channel_number}: {result}"
                }

            return {
                "ok": True,
                "channel": channel_number,
                "state": bool(on)
            }
        finally:
            client.close()

    def read_channels(self, start_channel=1, count=None):
        if count is None:
            count = self.channels
        if start_channel < 1 or start_channel > self.channels:
            raise ValueError(f"start_channel deve estar entre 1 e {self.channels}")
        if count < 1 or start_channel + count - 1 > self.channels:
            raise ValueError("Intervalo de leitura inválido")

        address = self._channel_to_address(start_channel)
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
                    "error": f"Erro Modbus ao ler canais: {result}"
                }

            states = []
            for index, bit in enumerate(result.bits[:count], start=start_channel):
                states.append({
                    "channel": index,
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
                values=[False] * self.channels,
                slave=self.unit_id
            )

            if result.isError():
                return {
                    "ok": False,
                    "error": f"Erro Modbus ao desligar todos os canais: {result}"
                }

            return {"ok": True}
        finally:
            client.close()


class RelayRouter:
    """Routes global relay numbers to the correct board+channel across multiple boards."""

    def __init__(self, config):
        self.config = config
        self._services = {}

    def _service_for(self, board):
        key = (board["host"], board["port"], board["unit_id"], board["channels"])
        if key not in self._services:
            timeout = self.config.get("system", {}).get("sensor_request_timeout_seconds", 2)
            self._services[key] = RelayBoardService(
                host=board["host"],
                port=board["port"],
                unit_id=board["unit_id"],
                channels=board["channels"],
                timeout=timeout,
            )
        return self._services[key]

    def set_relay(self, relay_number, on):
        board, channel_index = get_board_for_relay(self.config, relay_number)
        if board is None:
            return {
                "ok": False,
                "relay": relay_number,
                "error": f"Relé {relay_number} não mapeado a nenhum módulo"
            }
        service = self._service_for(board)
        result = service.set_channel(channel_index + 1, on)
        result["relay"] = relay_number
        result["board_id"] = board.get("id")
        return result

    def relay_on(self, relay_number):
        return self.set_relay(relay_number, True)

    def relay_off(self, relay_number):
        return self.set_relay(relay_number, False)

    def all_off(self):
        results = []
        for board in get_relay_boards(self.config):
            service = self._service_for(board)
            result = service.all_off()
            result["board_id"] = board.get("id")
            results.append(result)
        return results


def build_relay_board_service(config):
    return RelayRouter(config)
