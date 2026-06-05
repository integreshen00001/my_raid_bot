from flask import Flask
from threading import Thread
import raid_bot
import asyncio

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_bot():
    asyncio.run(raid_bot.main())

Thread(target=run_bot).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
