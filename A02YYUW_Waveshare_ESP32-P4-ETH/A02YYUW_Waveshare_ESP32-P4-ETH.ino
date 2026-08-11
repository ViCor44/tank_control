#include <Arduino.h>
#include <ETH.h>
#include <WebServer.h>
#include <math.h>

/* ============================================================
   WAVESHARE ESP32-P4-ETH + A02YYUW
   ============================================================

   A02YYUW:
     Vermelho -> 3V3
     Preto    -> GND
     Verde TX -> GPIO17
     Azul RX  -> NÃO LIGAR

   O sensor envia:
     FF | HIGH | LOW | CHECKSUM

   Distância RAW = milímetros

   Exemplo:
     RAW = 1234
     distância = 123.4 cm

   ============================================================ */


/* ============================================================
   ETHERNET RMII - WAVESHARE ESP32-P4-ETH

   PHY onboard: IP101GRI

   RMII fixo do ESP32-P4 / placa:
     TX_EN  -> GPIO49
     TXD0   -> GPIO34
     TXD1   -> GPIO35
     RXD0   -> GPIO29
     RXD1   -> GPIO30
     CRS_DV -> GPIO28
     REFCLK -> GPIO50 (50 MHz externo vindo do PHY)

   Gestão do PHY:
     MDC    -> GPIO31
     MDIO   -> GPIO52
     RESET  -> GPIO51

   Não existe W5500 nem SPI nesta placa.
   ============================================================ */

static const int ETH_MDC_PIN   = 31;
static const int ETH_MDIO_PIN  = 52;
static const int ETH_RESET_PIN = 51;


/* ============================================================
   REDE
   ============================================================ */

IPAddress local_IP(192, 168, 63, 142);
IPAddress gateway(192, 168, 63, 254);
IPAddress subnet(255, 255, 255, 0);
IPAddress dns1(8, 8, 8, 8);
IPAddress dns2(1, 1, 1, 1);

WebServer server(80);


/* ============================================================
   A02YYUW
   ============================================================ */

// TX verde do A02YYUW liga aqui
static const int SENSOR_RX_PIN = 17;

// Não precisamos de transmitir nada para o sensor
static const int SENSOR_TX_PIN = -1;

static const uint32_t SENSOR_BAUD = 9600;

HardwareSerial SensorSerial(1);


/* ============================================================
   CALIBRAÇÃO

   O A02YYUW já fornece milímetros.

   Assim:
      cm = 0.1 * raw

   Inicialmente NÃO ALTERAR estes valores.
   ============================================================ */

float A = 0.1f;
float B = 0.0f;


/* ============================================================
   FILTRO
   ============================================================ */

#define FILTER_SIZE 11

uint16_t samples[FILTER_SIZE];

int sampleCount = 0;
int sampleIndex = 0;


/* ============================================================
   ESTATÍSTICAS / DEBUG
   ============================================================ */

uint16_t lastRaw = 0;
uint16_t medianRaw = 0;

unsigned long lastValidFrame = 0;

uint32_t totalFrames = 0;
uint32_t checksumErrors = 0;
uint32_t rangeErrors = 0;


/* ============================================================
   LIMITES DO SENSOR
   ============================================================ */

// Aproximadamente 3 cm
const uint16_t SENSOR_MIN_MM = 30;

// Aproximadamente 4,5 m
const uint16_t SENSOR_MAX_MM = 4500;


/* ============================================================
   ESTADO DO PARSER UART
   ============================================================ */

enum ParserState {
  WAIT_HEADER,
  WAIT_HIGH,
  WAIT_LOW,
  WAIT_CHECKSUM
};

ParserState parserState = WAIT_HEADER;

uint8_t dataHigh = 0;
uint8_t dataLow = 0;


/* ============================================================
   CALCULAR MEDIANA
   ============================================================ */

uint16_t calculateMedian()
{
  if (sampleCount == 0)
    return 0;

  uint16_t temp[FILTER_SIZE];

  for (int i = 0; i < sampleCount; i++)
    temp[i] = samples[i];


  // Ordenação simples
  for (int i = 0; i < sampleCount - 1; i++) {

    for (int j = i + 1; j < sampleCount; j++) {

      if (temp[j] < temp[i]) {

        uint16_t t = temp[i];

        temp[i] = temp[j];
        temp[j] = t;
      }
    }
  }

  return temp[sampleCount / 2];
}


/* ============================================================
   GUARDAR NOVA LEITURA
   ============================================================ */

void addSample(uint16_t value)
{
  lastRaw = value;

  samples[sampleIndex] = value;

  sampleIndex++;

  if (sampleIndex >= FILTER_SIZE)
    sampleIndex = 0;


  if (sampleCount < FILTER_SIZE)
    sampleCount++;


  medianRaw = calculateMedian();

  lastValidFrame = millis();

  totalFrames++;
}


/* ============================================================
   PROCESSAR UART DO A02YYUW

   Não bloqueia o programa.
   ============================================================ */

void processSensor()
{
  while (SensorSerial.available()) {

    uint8_t b = SensorSerial.read();

    switch (parserState) {

      /* ------------------------------------------------------
         Esperar cabeçalho FF
         ------------------------------------------------------ */

      case WAIT_HEADER:

        if (b == 0xFF)
          parserState = WAIT_HIGH;

        break;


      /* ------------------------------------------------------
         Byte HIGH
         ------------------------------------------------------ */

      case WAIT_HIGH:

        dataHigh = b;

        parserState = WAIT_LOW;

        break;


      /* ------------------------------------------------------
         Byte LOW
         ------------------------------------------------------ */

      case WAIT_LOW:

        dataLow = b;

        parserState = WAIT_CHECKSUM;

        break;


      /* ------------------------------------------------------
         Checksum
         ------------------------------------------------------ */

      case WAIT_CHECKSUM:
      {
        uint8_t expectedChecksum =
            (uint8_t)(0xFF + dataHigh + dataLow);


        if (b == expectedChecksum) {

          uint16_t raw =
              ((uint16_t)dataHigh << 8) | dataLow;


          /* ----------------------------------------------
             Validar intervalo possível
             ---------------------------------------------- */

          if (raw >= SENSOR_MIN_MM &&
              raw <= SENSOR_MAX_MM) {

            addSample(raw);
          }
          else {

            rangeErrors++;
          }

        }
        else {

          checksumErrors++;
        }


        parserState = WAIT_HEADER;

        break;
      }
    }
  }
}


/* ============================================================
   DISTÂNCIA EM CM
   ============================================================ */

float getDistanceCm()
{
  if (medianRaw == 0)
    return 0;

  return A * medianRaw + B;
}


/* ============================================================
   ENDPOINT /

   Informação básica
   ============================================================ */

void handleRoot()
{
  String text;

  text += "A02YYUW Monitor - Waveshare ESP32-P4-ETH\n\n";

  text += "/distance\n";
  text += "/debug\n";
  text += "/set?a=0.1&b=0\n";
  text += "/cal?d1=20&raw1=200&d2=100&raw2=1000\n";

  server.send(
    200,
    "text/plain",
    text
  );
}


/* ============================================================
   ENDPOINT /distance
   ============================================================ */

void handleDistance()
{
  /*
     Consideramos leitura perdida se não recebermos
     nada durante mais de 2 segundos.
  */

  if (
      sampleCount == 0 ||
      millis() - lastValidFrame > 2000
     )
  {
    server.send(
      504,
      "application/json",
      "{\"ok\":false,\"error\":\"sensor_timeout\"}"
    );

    return;
  }


  float cm = getDistanceCm();


  String s = "{";

  s += "\"ok\":true,";

  s += "\"raw\":";
  s += String(medianRaw);
  s += ",";

  s += "\"distance_cm\":";
  s += String(cm, 1);
  s += ",";

  s += "\"samples\":";
  s += String(sampleCount);
  s += ",";

  s += "\"age_ms\":";
  s += String(millis() - lastValidFrame);

  s += "}";


  server.send(
    200,
    "application/json",
    s
  );
}


/* ============================================================
   ENDPOINT /debug

   Muito útil para diagnosticar leituras falsas.
   ============================================================ */

void handleDebug()
{
  String s = "{";

  s += "\"ok\":";

  if (sampleCount > 0)
    s += "true";
  else
    s += "false";


  s += ",";


  /* última leitura recebida */

  s += "\"last_raw\":";
  s += String(lastRaw);
  s += ",";


  /* mediana */

  s += "\"median_raw\":";
  s += String(medianRaw);
  s += ",";


  /* centímetros */

  s += "\"distance_cm\":";
  s += String(getDistanceCm(), 1);
  s += ",";


  /* calibração */

  s += "\"A\":";
  s += String(A, 8);
  s += ",";

  s += "\"B\":";
  s += String(B, 4);
  s += ",";


  /* estatísticas */

  s += "\"total_frames\":";
  s += String(totalFrames);
  s += ",";

  s += "\"checksum_errors\":";
  s += String(checksumErrors);
  s += ",";

  s += "\"range_errors\":";
  s += String(rangeErrors);
  s += ",";


  /* idade da última leitura */

  s += "\"age_ms\":";

  if (sampleCount > 0)
    s += String(millis() - lastValidFrame);
  else
    s += "-1";


  /* ----------------------------------------------
     Mostrar também as leituras do filtro
     ---------------------------------------------- */

  s += ",\"samples\":[";

  for (int i = 0; i < sampleCount; i++) {

    if (i > 0)
      s += ",";

    s += String(samples[i]);
  }

  s += "]";


  s += "}";


  server.send(
    200,
    "application/json",
    s
  );
}


/* ============================================================
   ENDPOINT /set

   Exemplo:

   /set?a=0.1&b=0

   ============================================================ */

void handleSet()
{
  if (server.hasArg("a"))
    A = server.arg("a").toFloat();


  if (server.hasArg("b"))
    B = server.arg("b").toFloat();


  String s = "{";

  s += "\"ok\":true,";

  s += "\"A\":";
  s += String(A, 8);
  s += ",";

  s += "\"B\":";
  s += String(B, 4);

  s += "}";


  server.send(
    200,
    "application/json",
    s
  );
}


/* ============================================================
   ENDPOINT /cal

   Calibração através de dois pontos.

   Exemplo:

   distância real 20 cm
   RAW = 202

   distância real 100 cm
   RAW = 1005

   /cal?d1=20&raw1=202&d2=100&raw2=1005

   ============================================================ */

void handleCal()
{
  if (
      !server.hasArg("d1") ||
      !server.hasArg("raw1") ||
      !server.hasArg("d2") ||
      !server.hasArg("raw2")
     )
  {
    server.send(
      400,
      "application/json",
      "{\"ok\":false,\"usage\":\"/cal?d1=20&raw1=200&d2=100&raw2=1000\"}"
    );

    return;
  }


  float d1 = server.arg("d1").toFloat();
  float d2 = server.arg("d2").toFloat();

  float r1 = server.arg("raw1").toFloat();
  float r2 = server.arg("raw2").toFloat();


  if (fabs(r2 - r1) < 0.0001f) {

    server.send(
      400,
      "application/json",
      "{\"ok\":false,\"error\":\"raw1_equals_raw2\"}"
    );

    return;
  }


  A = (d2 - d1) / (r2 - r1);

  B = d1 - A * r1;


  String s = "{";

  s += "\"ok\":true,";

  s += "\"A\":";
  s += String(A, 8);
  s += ",";

  s += "\"B\":";
  s += String(B, 4);

  s += "}";


  server.send(
    200,
    "application/json",
    s
  );
}


/* ============================================================
   SETUP
   ============================================================ */

void setup()
{
  Serial.begin(115200);

  delay(1000);


  Serial.println();
  Serial.println("====================================");
  Serial.println(" A02YYUW + Waveshare ESP32-P4-ETH");
  Serial.println("====================================");


  /* ----------------------------------------------------------
     UART SENSOR
     ---------------------------------------------------------- */

  SensorSerial.begin(
    SENSOR_BAUD,
    SERIAL_8N1,
    SENSOR_RX_PIN,
    SENSOR_TX_PIN
  );


  Serial.println("A02YYUW iniciado a 9600 baud");

  Serial.print("GPIO RX do sensor: ");
  Serial.println(SENSOR_RX_PIN);


  /* ----------------------------------------------------------
     ETHERNET RMII / IP101GRI
     ---------------------------------------------------------- */

  Serial.println("A iniciar Ethernet RMII...");

  bool ethOk = ETH.begin(
    ETH_PHY_IP101,
    ETH_PHY_ADDR_AUTO,
    ETH_MDC_PIN,
    ETH_MDIO_PIN,
    ETH_RESET_PIN,
    EMAC_CLK_EXT_IN
  );

  if (!ethOk) {
    Serial.println("ERRO: ETH.begin() falhou.");
  }
  else {
    Serial.println("Driver Ethernet iniciado.");
  }


  /* ----------------------------------------------------------
     IP ESTÁTICO
     ---------------------------------------------------------- */

  if (!ETH.config(
        local_IP,
        gateway,
        subnet,
        dns1,
        dns2
      ))
  {
    Serial.println("ERRO: não foi possível configurar o IP estático.");
  }


  delay(500);


  Serial.print("IP configurado: ");
  Serial.println(ETH.localIP());

  Serial.print("MAC Ethernet: ");
  Serial.println(ETH.macAddress());


  /* ----------------------------------------------------------
     SERVIDOR WEB
     ---------------------------------------------------------- */

  server.on(
    "/",
    HTTP_GET,
    handleRoot
  );


  server.on(
    "/distance",
    HTTP_GET,
    handleDistance
  );


  server.on(
    "/debug",
    HTTP_GET,
    handleDebug
  );


  server.on(
    "/set",
    HTTP_GET,
    handleSet
  );


  server.on(
    "/cal",
    HTTP_GET,
    handleCal
  );


  server.begin();


  Serial.println("Servidor HTTP iniciado");
  Serial.println("Endpoints:");
  Serial.println("  http://192.168.63.141/");
  Serial.println("  http://192.168.63.141/distance");
  Serial.println("  http://192.168.63.141/debug");
  Serial.println();
}


/* ============================================================
   LOOP
   ============================================================ */

void loop()
{
  /*
     A leitura do sensor é contínua e independente
     dos pedidos HTTP.
  */

  processSensor();


  /*
     Servidor HTTP
  */

  server.handleClient();
}
