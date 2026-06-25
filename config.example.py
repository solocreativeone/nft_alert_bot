import os


# COPY THIS FILE TO private/config_live.py
# Fill in your real values — never commit that file


TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")   # From @BotFather
CHAT_ID = os.environ.get("CHAT_ID")                 # Your Telegram chat ID
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY") # From opensea.io/developers

# Collections to watch
COLLECTIONS = [
    {
        "name": "Bored Ape Yacht Club",
        "slug": "boredapeyachtclub",
        "contract": "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
        "floor_alert_low": 10.0,    # Alert if floor drops BELOW this (ETH)
        "floor_alert_high": 20.0,   # Alert if floor rises ABOVE this (ETH)
    },
    {
        "name": "Pudgy Penguins",
        "slug": "pudgypenguins",
        "contract": "0xBd3531dA5CF5857e7CfAA92426877b022e612cf8",
        "floor_alert_low": 5.0,
        "floor_alert_high": 15.0,
    },
    
    {
        "name": "Signal Bound",
        "slug": "signal-bound",
        "contract": "0x3512ba948a032b00952cc6ba43bc013b4fcf7ebc",
        "floor_alert_low": 0.05,
        "floor_alert_high": 1.0,
    },
    
    {
        "name": "Cool I Guess Crew",
        "slug": "cool-i-guess-crew",
        "contract": "0x6de7848a77e0910b29723dba879fcba3d8c07b67",
        "floor_alert_low": 0.01,
        "floor_alert_high": 0.5,
    },
    # Add more collections here...
]

# How often to poll (in minutes)
FLOOR_CHECK_INTERVAL = 5
MINT_CHECK_INTERVAL = 1