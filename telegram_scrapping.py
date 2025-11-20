from telethon import TelegramClient
import os

api_id = 33649551
api_hash = "8576b5c2ec5c9e758c8fa780b12306a0"

client = TelegramClient("session", api_id, api_hash)

async def run():
    channel = "https://t.me/InfoSportPlusfooT"

    async for msg in client.iter_messages(channel, limit=200):
        print(msg.id, msg.text)

        # Télécharger les fichiers
        # if msg.media:
        #     await msg.download_media(file=os.path.join("downloads", f"{msg.id}"))

with client:
    client.loop.run_until_complete(run())
