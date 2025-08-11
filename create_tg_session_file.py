from telethon import TelegramClient
import os

app_id = 11223344  # Replace with your actual app_id
app_hash = 'aabbee44ii55oro0324i4k9f32k4f90g' # Replace with your actual app_hash


if os.path.exists('user_session.session'):
    print("Session file already exists.")
else:
    client = TelegramClient('user_session', app_id, app_hash)
    if not os.path.exists('user_session.session'):
        print("Session file created failed.")
    else:
        print("Session file created successfully.")
        print("Session file path:", os.path.abspath('user_session.session'))