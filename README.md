# wallet-watcher

`wallet-watcher` отслеживает публичные EVM-адреса в разных сетях и отправляет уведомления в Telegram только при новых исходящих движениях:

- обычные исходящие транзакции native coin, где `from` равен адресу вашего кошелька
- исходящие ERC-20 token transfers, где `from` равен адресу вашего кошелька

Скрипт не использует seed phrase, private key, пароль от кошелька, браузерные профили, расширения кошельков, подписи транзакций или любые действия с кошельками. Он только читает публичные данные explorer/API.

## Поддерживаемые сети в v1

- Ethereum
- Base
- Linea
- Plume
- Botanix
- Scroll
- Arbitrum
- Polygon
- Avalanche
- opBNB
- Zora
- Taiko
- Gravity
- Optimism / OP Mainnet

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
BLOCKSCOUT_API_KEY=your_blockscout_api_key
BOTANIXSCAN_API_KEY=your_botanixscan_api_key
BLOCKPI_BASE_RPC_URL=
BLOCKPI_AVALANCHE_RPC_URL=
BLOCKPI_OPTIMISM_RPC_URL=
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
API_CHECK_INTERVAL_SECONDS=300
BLOCKPI_CHECK_INTERVAL_SECONDS=300
MAX_EVENT_AGE_SECONDS=86400
```

`.env` добавлен в `.gitignore`. Не коммитьте его, не вставляйте в чаты и не храните секреты в истории shell.

Если у вас несколько API-ключей или BlockPI endpoint-ов, укажите их через запятую. Скрипт будет стабильно распределять кошельки между ключами по адресу:

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
- дополнительные поля endpoint-ов для Blockscout/Botanix

Если endpoint для Blockscout или Botanix отличается, поправьте эти поля в `networks.json`:

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

Для Etherscan-style API у BotanixScan можно настроить account API actions:

```json
"api_base_url": "https://api.routescan.io/v2/network/mainnet/evm/3637/etherscan/api",
"native_action": "txlist",
"token_action": "tokentx"
```

Base, Avalanche и Optimism можно мониторить через BlockPI RPC endpoint-ы. URL с ключами храните только в `.env`:

```bash
BLOCKPI_BASE_RPC_URL=https://base.blockpi.network/v1/rpc/your-rpc-key
BLOCKPI_AVALANCHE_RPC_URL=https://avalanche.blockpi.network/v1/rpc/your-rpc-key
BLOCKPI_OPTIMISM_RPC_URL=https://optimism-classic.blockpi.network/v1/rpc/your-rpc-key
```

Для этих сетей provider `blockpi_rpc` сканирует последние блоки:

- native outgoing tx ищутся в транзакциях блоков
- ERC-20 outgoing transfers ищутся через `eth_getLogs` по событию `Transfer`

Количество блоков задается в `networks.json` через `rpc_scan_blocks`. По умолчанию для этих сетей стоит `300`, чтобы оставаться ниже типичных RPC-лимитов на диапазон `eth_getLogs`.

BlockPI сети проверяются отдельным интервалом:

```bash
BLOCKPI_CHECK_INTERVAL_SECONDS=300
```

Дефолт `300` секунд выбран так, чтобы окно `rpc_scan_blocks=300` не пропускало события на быстрых EVM-сетях. Если поставить большой интервал, например 30 часов, то текущая реализация будет видеть только последние `rpc_scan_blocks` блоков и может пропустить исходящие транзакции.

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

Команда выводит диагностику по каждой сети:

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

API-ключи в диагностике маскируются. Показываются только первые 4 и последние 4 символа.

## Однократная проверка

```bash
python main.py --once
```

При первом запуске скрипт сохраняет текущие последние исходящие транзакции в `state.json` и пишет в лог `Initial state saved`. Старые транзакции в Telegram не отправляются.

## Обычный мониторинг

```bash
python main.py
```

По умолчанию обычные explorer/API сети и BlockPI сети проверяются каждые 5 минут. Интервалы можно изменить в `.env`:

```bash
API_CHECK_INTERVAL_SECONDS=300
BLOCKPI_CHECK_INTERVAL_SECONDS=300
```

Старый `CHECK_INTERVAL_SECONDS` еще поддерживается как fallback для `API_CHECK_INTERVAL_SECONDS`, если новая переменная не указана.

Для большого числа кошельков, например 100+, текущий BlockPI provider может быть дорогим по RPC-запросам, потому что сканирует блоки отдельно для каждого кошелька. Для такого масштаба лучше доработать сканирование по сети один раз за цикл и сравнивать найденные транзакции со всеми адресами.

`MAX_EVENT_AGE_SECONDS=86400` запрещает отправлять Telegram-alert по транзакциям старше 24 часов. Старые hashes при этом сохраняются в `state.json`, чтобы они не всплывали повторно.

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

`state.json` хранит обработанные hashes по ключу:

```text
network:wallet_address:event_type
```

Пример:

```json
{
  "Ethereum:0x123:native": ["0xhash1"],
  "Ethereum:0x123:erc20": ["0xhash2"]
}
```

Адреса сравниваются case-insensitive. Уже обработанные hashes повторно в Telegram не отправляются.

## Логи

Ошибки runtime пишутся в:

```text
logs/events.log
```

API-ключи и Telegram tokens приложение в логи не пишет.
