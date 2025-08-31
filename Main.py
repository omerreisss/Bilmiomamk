from telethon import TelegramClient
import asyncio

# Telegram API bilgilerin
api_id = 26633883
api_hash = "53c5be7ad8e3bf758bd000624a799a78"

# Client oluşturma
client = TelegramClient('session_name', api_id, api_hash)

async def send_messages():
    while True:
        async for dialog in client.iter_dialogs():
            try:
                await client.send_message(dialog.id, "Vip+ p0rn0 paylaşım grubumuza biomdaki linkten katılabilirsiniz💋.")
                print(f"Mesaj gönderildi: {dialog.name}")
            except Exception as e:
                print(f"Hata: {dialog.name} -> {e}")
        await asyncio.sleep(600)  # 10 dakika bekle

with client:
    client.loop.run_until_complete(send_messages())
