# tank_control

Aplicação web para gestão de tanques, fontes, válvulas e regras de enchimento com Raspberry Pi.

## Execução

```powershell
python app.py
```

O leitor de sensores arranca automaticamente em segundo plano. Não é necessário
executar `sensor_poller.py` separadamente. Isto também se aplica ao arranque por
`flask run` ou através de um servidor WSGI.

## Primeiro acesso

O Dashboard é público. As restantes áreas pedem o PIN de um utilizador. A roda
dentada abre a administração com o PIN master inicial `1234`; altere-o no primeiro
acesso. Em produção, pode definir o primeiro PIN através da variável de ambiente
`TANK_CONTROL_MASTER_PIN` antes de iniciar a aplicação.
