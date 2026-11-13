# wallet-watcher

`wallet-watcher` отслеживает публичные EVM-адреса в разных сетях и отправляет уведомления в Telegram только при новых исходящих движениях:

- обычные исходящие транзакции native coin, где `from` равен адресу вашего кошелька
- исходящие ERC-20 token transfers, где `from` равен адресу вашего кошелька

Скрипт не использует seed phrase, private key, пароль от кошелька, браузерные профили, расширения кошельков, подписи транзакций или любые действия с кошельками. Он только читает публичные данные explorer/API.

## Поддерживаемые сети

| Сеть | Источник данных | Что нужно |
|---|---|---|
| Ethereum, Linea, Arbitrum, Polygon, Taiko | Etherscan API V2 (`etherscan_v2`) | `ETHERSCAN_API_KEY`, хватает бесплатного тарифа |
| Base, Avalanche, Optimism | RPC (`rpc`) | RPC URL в `.env`, например BlockPI |
| opBNB, Gravity | RPC (`rpc`) | ничего, используется публичный RPC из `networks.json` |
| Scroll, Plume | Blockscout API (`blockscout`) | ничего |
| Zora | Blockscout API | выключена: обозреватель закрыт защитой Cloudflare |

Base, Optimism и Avalanche на бесплатном тарифе Etherscan недоступны, opBNB и Scroll Etherscan V2 не поддерживает вообще, поэтому они читаются через RPC и Blockscout. Botanix удалён: его обозреватель и API больше не работают.

## Установка зависимостей

Нужен Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка `.env`

Создайте локальный `.env` из примера:

```bash
cp .env.example .env
```

Заполните переменные:

```bash
ETHERSCAN_API_KEY=your_etherscan_api_key
BLOCKSCOUT_API_KEY=
BLOCKPI_BASE_RPC_URL=
BLOCKPI_AVALANCHE_RPC_URL=
BLOCKPI_OPTIMISM_RPC_URL=
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
API_CHECK_INTERVAL_SECONDS=300
RPC_CHECK_INTERVAL_SECONDS=120
MAX_EVENT_AGE_SECONDS=86400
ETHERSCAN_CALLS_PER_SECOND=3
NETWORK_FAILURE_ALERT_CYCLES=3
```

`.env` добавлен в `.gitignore`. Не коммитьте его, не вставляйте в чаты и не храните секреты в истории shell.

Если у вас несколько API-ключей Etherscan, укажите их через запятую — кошельки стабильно распределяются между ключами по адресу. Несколько RPC URL через запятую работают как резерв: если первый endpoint не отвечает, скан повторяется на следующем.

```bash
ETHERSCAN_API_KEY=key_1,key_2,key_3
BLOCKPI_BASE_RPC_URL=https://base.blockpi.network/v1/rpc/key_1,https://base.blockpi.network/v1/rpc/key_2
```

## Настройка кошельков

Создайте локальный `wallets.json` из публичного примера:

```bash
cp wallets.example.json wallets.json
```

Отредактируйте `wallets.json`:

```json
[
  {
    "name": "Wallet 01",
    "address": "0x0000000000000000000000000000000000000000",
    "enabled": true
  }
]
```

Используйте только публичные адреса кошельков. Чтобы временно отключить кошелек, поставьте `"enabled": false`.

`wallets.json` добавлен в `.gitignore`, потому что реальные адреса могут быть приватными операционными данными. В репозиторий коммитьте только `wallets.example.json`.

## Настройка сетей

`networks.json` содержит:

- `chain_id`
- `native_symbol`
- `explorer_url`
- `api_provider`
- `enabled`
- дополнительные поля endpoint-ов для Blockscout и RPC

Чтобы временно отключить проблемную сеть, добавьте или измените:

```json
"enabled": false
```

Если endpoint для Blockscout отличается, поправьте эти поля в `networks.json`:

```json
"api_base_url": "https://example.com/api/v2",
"transactions_endpoint": "/addresses/{address}/transactions",
"token_transfers_endpoint": "/addresses/{address}/token-transfers"
```

Для API-ключей Blockscout можно также настроить:

```json
"api_key_header": "Authorization",
"api_key_prefix": "Bearer "
```

### RPC-сети

Сеть с `"api_provider": "rpc"` читается напрямую через JSON-RPC. URL задаётся одним из двух способов:

```json
"rpc_url_env": "BLOCKPI_BASE_RPC_URL"
```

```json
"rpc_url": "https://rpc.gravity.xyz"
```

`rpc_url_env` — имя переменной из `.env`, так хранятся URL с ключами. `rpc_url` — публичный endpoint без секрета. Можно указать оба: публичный будет резервным.

```bash
BLOCKPI_BASE_RPC_URL=https://base.blockpi.network/v1/rpc/your-rpc-key
BLOCKPI_AVALANCHE_RPC_URL=https://avalanche.blockpi.network/v1/rpc/your-rpc-key
BLOCKPI_OPTIMISM_RPC_URL=https://optimism-classic.blockpi.network/v1/rpc/your-rpc-key
```

Как работает сканирование:

- сеть сканируется один раз за цикл сразу для всех кошельков
- в `state.json` хранится номер последнего проверенного блока; следующий цикл продолжает с него, поэтому простой или долгий интервал не приводят к пропускам
- исходящие ERC-20 transfers ищутся через `eth_getLogs` по событию `Transfer` на всём диапазоне новых блоков
- исходящие транзакции определяются по nonce кошелька (`eth_getTransactionCount`); блоки скачиваются, только если nonce вырос
- если nonce вырос, а транзакция в блоках не найдена, приходит alert со ссылкой на адрес в обозревателе

Необязательные поля в `networks.json`:

| Поле | По умолчанию | Смысл |
|---|---|---|
| `rpc_logs_chunk_blocks` | 300 | диапазон блоков одного `eth_getLogs`; при отказе узла уменьшается автоматически |
| `rpc_batch_size` | 10 | сколько блоков запрашивать одним batch-запросом |
| `rpc_native_scan_max_blocks` | 3000 | сколько последних блоков просматривать в поисках транзакции |
| `rpc_max_catchup_blocks` | 50000 | предел догоняющего скана после долгого простоя |

Ограничения: через nonce не видны переводы из смарт-кошельков (Safe, ERC-4337) и внутренние переводы контрактов; NFT (ERC-721/1155) не отслеживаются.

RPC-сети проверяются отдельным интервалом:

```bash
RPC_CHECK_INTERVAL_SECONDS=120
```

Старое имя `BLOCKPI_CHECK_INTERVAL_SECONDS` и старое значение `"api_provider": "blockpi_rpc"` по-прежнему принимаются.

## Проверка Telegram

```bash
python main.py --test-telegram
```

Команда отправляет тестовое сообщение:

```text
Wallet watcher test message
```

## Проверка API

```bash
python main.py --test-api
```

Команда выводит диагностику по каждой включённой сети:

```text
Network: Ethereum
  Provider: etherscan_v2
  Chain ID: 1
  Endpoint: https://api.etherscan.io/v2/api?module=account&action=txlist&...
  API key: abcd...wxyz
  Response received: YES
  HTTP status: 200
  Auth error: NO
  Tariff / unsupported chain error: NO
  Rate limit error: NO
  Status: OK
  Message: OK
```

Для Blockscout проверяются оба запроса (транзакции и token transfers), для RPC — `eth_blockNumber`, `eth_getLogs` на рабочем диапазоне и batch-запросы. API-ключи в диагностике маскируются.

## Однократная проверка

```bash
python main.py --once
```

При первом запуске скрипт сохраняет текущие последние исходящие транзакции в `state.json` и пишет в лог `Initial state saved`. Старые транзакции в Telegram не отправляются.

## Обычный мониторинг

```bash
python main.py
```

Интервалы проверки задаются в `.env`:

```bash
API_CHECK_INTERVAL_SECONDS=300
RPC_CHECK_INTERVAL_SECONDS=120
```

Старый `CHECK_INTERVAL_SECONDS` ещё поддерживается как fallback для `API_CHECK_INTERVAL_SECONDS`.

Etherscan: каждый кошелёк тратит 2 запроса на сеть за цикл. При интервале 300 секунд и 5 сетях это около 2 900 запросов в сутки на кошелёк. Если расчёт превышает 100 000 запросов в сутки на ключ, при старте в лог пишется предупреждение — добавьте ключи через запятую или увеличьте интервал. `ETHERSCAN_CALLS_PER_SECOND` ограничивает частоту запросов на один ключ.

`MAX_EVENT_AGE_SECONDS=86400` запрещает отправлять Telegram-alert по транзакциям старше 24 часов. Они всё равно помечаются обработанными в `state.json`.

Если Telegram недоступен, alert не теряется: он сохраняется в `state.json` и отправляется при следующем цикле.

Если сеть не отвечает `NETWORK_FAILURE_ALERT_CYCLES` циклов подряд (по умолчанию 3), в Telegram приходит одно предупреждение, а после восстановления — сообщение, что сеть снова работает. `0` отключает эти сообщения.

Если новых исходящих транзакций или token transfers нет, Telegram молчит.

## Подготовка к GitHub

URL репозитория:

```text
https://github.com/OlegAA1/wallet-watcher
```

Проект подготовлен так, чтобы эти файлы не попадали в коммит:

- `.env`
- `state.json`
- `wallets.json`
- `logs/`
- `__pycache__/`
- `*.pyc`
- `.venv/`
- `venv/`

В репозиторий добавляются только файлы-примеры:

- `.env.example`
- `wallets.example.json`
- `state.example.json`

Первый push:

```bash
git init
git add .
git commit -m "Initial wallet watcher project"
git branch -M main
git remote add origin https://github.com/OlegAA1/wallet-watcher.git
git push -u origin main
```

Если `origin` уже существует:

```bash
git remote set-url origin https://github.com/OlegAA1/wallet-watcher.git
git push -u origin main
```

Перед push проверьте, что попадет в коммит:

```bash
git status --short
```

Не пушьте реальные API-ключи, Telegram token, Telegram chat ID, реальные списки кошельков, seed phrase, private key, wallet password или любые другие приватные данные.

## Запуск на VPS через tmux или screen

Через `tmux`:

```bash
tmux new -s wallet-watcher
source .venv/bin/activate
python main.py
```

Отключиться от сессии: `Ctrl-b`, затем `d`. Вернуться позже:

```bash
tmux attach -t wallet-watcher
```

Через `screen`:

```bash
screen -S wallet-watcher
source .venv/bin/activate
python main.py
```

Отключиться от сессии: `Ctrl-a`, затем `d`. Вернуться позже:

```bash
screen -r wallet-watcher
```

## Deploy на VPS через systemd

Systemd-сервис запускает обычный бесконечный мониторинг:

```bash
python main.py
```

Режимы `--once`, `--test-api` и `--test-telegram` запускаются вручную, когда нужны.

Предполагаемый путь проекта на сервере:

```text
/opt/wallet-watcher
```

Пользователь сервиса:

```text
walletwatcher
```

1. Создать пользователя сервиса:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin walletwatcher
```

2. Склонировать проект:

```bash
sudo git clone https://github.com/OlegAA1/wallet-watcher.git /opt/wallet-watcher
```

3. Назначить права:

```bash
sudo chown -R walletwatcher:walletwatcher /opt/wallet-watcher
```

4. Создать виртуальное окружение:

```bash
cd /opt/wallet-watcher
sudo -u walletwatcher python3 -m venv .venv
sudo -u walletwatcher .venv/bin/pip install -r requirements.txt
```

5. Создать `.env`:

```bash
sudo -u walletwatcher cp .env.example .env
sudo nano .env
```

6. Создать `wallets.json`:

```bash
sudo -u walletwatcher cp wallets.example.json wallets.json
sudo nano wallets.json
```

7. Создать systemd service файл:

```bash
sudo nano /etc/systemd/system/wallet-watcher.service
```

Содержимое файла:

```ini
[Unit]
Description=Wallet Watcher Telegram Alerts
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=walletwatcher
Group=walletwatcher
WorkingDirectory=/opt/wallet-watcher
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

8. Перезагрузить systemd:

```bash
sudo systemctl daemon-reload
```

9. Включить автозапуск:

```bash
sudo systemctl enable wallet-watcher
```

10. Запустить сервис:

```bash
sudo systemctl start wallet-watcher
```

11. Проверить статус:

```bash
sudo systemctl status wallet-watcher
```

12. Смотреть логи:

```bash
journalctl -u wallet-watcher -f
```

Логи приложения также пишутся сюда:

```text
/opt/wallet-watcher/logs/events.log
```

После изменения `.env` или `wallets.json` перезапустите сервис:

```bash
sudo systemctl restart wallet-watcher
```

### Тестовые команды на сервере

Проверить Telegram:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --test-telegram
```

Проверить API:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --test-api
```

Запустить одну проверку:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --once
```

## State и защита от дублей

`state.json` хранит обработанные события по ключу:

```text
network:wallet_address:event_type
```

Пример:

```json
{
  "Ethereum:0x123:native": ["0xhash1"],
  "Ethereum:0x123:erc20": ["0xhash2:3"],
  "_meta": {
    "rpc": {"Base": {"last_block": 56994633, "nonces": {"0x123": 42}}},
    "pending_alerts": []
  }
}
```

Token transfers идентифицируются хешем транзакции и позицией перевода, поэтому несколько переводов в одной транзакции дают отдельные alerts. Раздел `_meta` хранит последний проверенный блок и nonce для RPC-сетей, а также alerts, которые ещё не доставлены в Telegram. `state.json` от старых версий подходит без изменений.

Адреса сравниваются case-insensitive. Уже обработанные события повторно в Telegram не отправляются.

## Тесты

```bash
python -m unittest discover -s tests -t .
```

Тесты не ходят в сеть.

## Логи

Ошибки runtime пишутся в:

```text
logs/events.log
```

Файл ротируется: до 5 МБ, хранятся 3 предыдущих файла.

API-ключи и Telegram tokens приложение в логи не пишет.
