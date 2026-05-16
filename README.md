# wallet-watcher

`wallet-watcher` monitors public EVM wallet addresses across configured networks and sends Telegram alerts only for new outgoing movements:

- native coin transactions where `from` equals your wallet address
- ERC-20 token transfers where `from` equals your wallet address

It never uses seed phrases, private keys, wallet passwords, browser profiles, wallet extensions, signatures, or wallet actions. The script only reads public explorer/API data.

## Supported networks in v1

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

## Install dependencies

Use Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure `.env`

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Fill in:

```bash
ETHERSCAN_API_KEY=your_etherscan_api_key
BLOCKSCOUT_API_KEY=your_blockscout_api_key
BOTANIXSCAN_API_KEY=your_botanixscan_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
CHECK_INTERVAL_SECONDS=300
```

`.env` is listed in `.gitignore`. Do not commit it, paste it into chats, or store it in shell history.

## Configure wallets

Create a local `wallets.json` file from the public example:

```bash
cp wallets.example.json wallets.json
```

Edit `wallets.json`:

```json
[
  {
    "name": "Wallet 01",
    "address": "0x0000000000000000000000000000000000000000",
    "enabled": true
  }
]
```

Use only public wallet addresses. Set `"enabled": false` to temporarily skip a wallet.

`wallets.json` is listed in `.gitignore`, because real wallet addresses can be private operational data. Commit only `wallets.example.json`.

## Configure networks

`networks.json` contains:

- `chain_id`
- `native_symbol`
- `explorer_url`
- `api_provider`
- optional Blockscout/Botanix API endpoint fields

If a Blockscout or Botanix endpoint differs, update these fields in `networks.json`:

```json
"api_base_url": "https://example.com/api/v2",
"transactions_endpoint": "/addresses/{address}/transactions",
"token_transfers_endpoint": "/addresses/{address}/token-transfers"
```

For Blockscout API keys, you can also adjust:

```json
"api_key_header": "Authorization",
"api_key_prefix": "Bearer "
```

For BotanixScan-compatible APIs, you can adjust account API actions:

```json
"native_action": "txlist",
"token_action": "tokentx"
```

## Test Telegram

```bash
python main.py --test-telegram
```

This sends:

```text
Wallet watcher test message
```

## Test APIs

```bash
python main.py --test-api
```

The command prints every network with provider status:

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

API keys are masked in diagnostics. Only the first 4 and last 4 characters are shown.

## Run one check

```bash
python main.py --once
```

On first run, the script saves the current latest outgoing transactions into `state.json` and logs `Initial state saved`. It does not send Telegram alerts for old history.

## Run monitoring

```bash
python main.py
```

By default it checks every 5 minutes. Change this with `CHECK_INTERVAL_SECONDS` in `.env`.

If there are no new outgoing transactions or token transfers, Telegram stays silent.

## Prepare for GitHub

Repository URL:

```text
https://github.com/OlegAA1/wallet-watcher
```

The repository is prepared so these files are not committed:

- `.env`
- `state.json`
- `wallets.json`
- `logs/`
- `__pycache__/`
- `*.pyc`
- `.venv/`
- `venv/`

Commit the example files instead:

- `.env.example`
- `wallets.example.json`
- `state.example.json`

First push:

```bash
git init
git add .
git commit -m "Initial wallet watcher project"
git branch -M main
git remote add origin https://github.com/OlegAA1/wallet-watcher.git
git push -u origin main
```

If `origin` already exists:

```bash
git remote set-url origin https://github.com/OlegAA1/wallet-watcher.git
git push -u origin main
```

Before pushing, check what will be committed:

```bash
git status --short
```

Do not push real API keys, Telegram tokens, Telegram chat IDs, real wallet lists, seed phrases, private keys, wallet passwords, or any other private data.

## Run on VPS with tmux or screen

With `tmux`:

```bash
tmux new -s wallet-watcher
source .venv/bin/activate
python main.py
```

Detach with `Ctrl-b`, then `d`. Reattach later:

```bash
tmux attach -t wallet-watcher
```

With `screen`:

```bash
screen -S wallet-watcher
source .venv/bin/activate
python main.py
```

Detach with `Ctrl-a`, then `d`. Reattach later:

```bash
screen -r wallet-watcher
```

## Deploy on VPS with systemd

The systemd service runs the normal infinite monitoring mode:

```bash
python main.py
```

Run `--once`, `--test-api`, and `--test-telegram` manually when needed.

Assumed server path:

```text
/opt/wallet-watcher
```

Service user:

```text
walletwatcher
```

1. Create the service user:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin walletwatcher
```

2. Clone the project:

```bash
sudo git clone https://github.com/OlegAA1/wallet-watcher.git /opt/wallet-watcher
```

3. Assign ownership:

```bash
sudo chown -R walletwatcher:walletwatcher /opt/wallet-watcher
```

4. Create the virtual environment:

```bash
cd /opt/wallet-watcher
sudo -u walletwatcher python3 -m venv .venv
sudo -u walletwatcher .venv/bin/pip install -r requirements.txt
```

5. Create `.env`:

```bash
sudo -u walletwatcher cp .env.example .env
sudo nano .env
```

6. Create `wallets.json`:

```bash
sudo -u walletwatcher cp wallets.example.json wallets.json
sudo nano wallets.json
```

7. Create the systemd service file:

```bash
sudo nano /etc/systemd/system/wallet-watcher.service
```

Use this content:

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

8. Reload systemd:

```bash
sudo systemctl daemon-reload
```

9. Enable autostart:

```bash
sudo systemctl enable wallet-watcher
```

10. Start the service:

```bash
sudo systemctl start wallet-watcher
```

11. Check status:

```bash
sudo systemctl status wallet-watcher
```

12. Watch logs:

```bash
journalctl -u wallet-watcher -f
```

Application logs are also written to:

```text
/opt/wallet-watcher/logs/events.log
```

After changing `.env` or `wallets.json`, restart the service:

```bash
sudo systemctl restart wallet-watcher
```

### Server test commands

Test Telegram:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --test-telegram
```

Test APIs:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --test-api
```

Run one scan:

```bash
sudo -u walletwatcher /opt/wallet-watcher/.venv/bin/python /opt/wallet-watcher/main.py --once
```

## State and duplicate protection

`state.json` stores processed hashes by:

```text
network:wallet_address:event_type
```

Example:

```json
{
  "Ethereum:0x123:native": ["0xhash1"],
  "Ethereum:0x123:erc20": ["0xhash2"]
}
```

Addresses are compared case-insensitively. Already processed hashes are not alerted again.

## Logs

Runtime errors are written to:

```text
logs/events.log
```

API keys and Telegram tokens are not written to logs by the application.
