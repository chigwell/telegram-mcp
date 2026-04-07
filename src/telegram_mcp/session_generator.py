#!/usr/bin/env python3
"""
Telegram Session String Generator

This script generates a session string that can be used for Telegram authentication
with the Telegram MCP server. The session string allows for portable authentication
without storing session files.

Usage:
    python session_string_generator.py

Requirements:
    - telethon
    - python-dotenv

Note on ID Formats:
When using the MCP server, please be aware that all `chat_id` and `user_id`
parameters support integer IDs, string representations of IDs (e.g., "123456"),
and usernames (e.g., "@mychannel").
"""

import asyncio
import io
import os
import sys

from dotenv import load_dotenv
from telethon import errors
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

load_dotenv()


def _qr_login(client: TelegramClient) -> None:
    import qrcode

    qr = client.qr_login()

    print("\n----- QR Code Login -----\n")

    qr_obj = qrcode.QRCode(border=1)
    qr_obj.add_data(qr.url)
    qr_obj.make(fit=True)
    f = io.StringIO()
    qr_obj.print_ascii(out=f, invert=True)
    print(f.getvalue())

    print("Scan the QR code above with your Telegram app:")
    print("  Open Telegram > Settings > Devices > Link Desktop Device\n")
    print(f"Or open this link on a device where you're logged in:\n  {qr.url}\n")
    print(f"Expires at: {qr.expires.strftime('%H:%M:%S')}")
    print("Waiting for you to scan...")

    try:
        client.loop.run_until_complete(qr.wait(timeout=120))
    except asyncio.TimeoutError:
        print("\nQR code expired. Please try again.")
        client.disconnect()
        sys.exit(1)
    except errors.SessionPasswordNeededError:
        pw = input("\nTwo-factor authentication enabled. Please enter your password: ")
        client.sign_in(password=pw)


def _phone_login(client: TelegramClient) -> None:
    phone = input("Please enter your phone (or bot token): ")

    try:
        client.send_code_request(phone)
    except errors.FloodWaitError as e:
        print(f"\nFlood wait error; you must wait {e.seconds} seconds before trying again.")
        client.disconnect()
        sys.exit(1)
    except errors.PhoneNumberInvalidError:
        print("\nThe phone number is invalid.")
        client.disconnect()
        sys.exit(1)
    except Exception as e:
        print(f"\nError sending code: {e}")
        client.disconnect()
        sys.exit(1)

    code = input("\nPlease enter the code you received: ")
    try:
        client.sign_in(phone, code)
    except errors.SessionPasswordNeededError:
        pw = input("Two-factor authentication enabled. Please enter your password: ")
        client.sign_in(password=pw)


def main() -> None:
    print("\n----- Telegram Session String Generator -----\n")
    print("This script will generate a session string for your Telegram account.")
    print("The generated session string can be added to your .env file.")
    print(
        "\nYour credentials will NOT be stored on any server and are only used for local authentication.\n"
    )

    API_ID_str = os.getenv("TELEGRAM_API_ID")
    API_HASH = os.getenv("TELEGRAM_API_HASH")

    if not API_ID_str or not API_HASH:
        print("API credentials not found in environment.")
        print("You can get your API ID and Hash from https://my.telegram.org/apps\n")
        API_ID_str = input("Enter your TELEGRAM_API_ID: ").strip()
        API_HASH = input("Enter your TELEGRAM_API_HASH: ").strip()

    if not API_ID_str or not API_HASH:
        print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH are absolutely required.")
        sys.exit(1)

    try:
        API_ID = int(API_ID_str)
    except ValueError:
        print("Error: TELEGRAM_API_ID must be an integer")
        sys.exit(1)

    print("Choose login method:")
    print("  1) QR code login (recommended -- scan from your Telegram app)")
    print("  2) Phone number + verification code")
    method = input("\nEnter 1 or 2 [default: 1]: ").strip() or "1"

    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        client.connect()

        if not client.is_user_authorized():
            if method == "1":
                _qr_login(client)
            else:
                _phone_login(client)

        session_string = StringSession.save(client.session)

        print("\nAuthentication successful!")
        print("\n----- Your Session String -----")
        print(f"\n{session_string}\n")
        print("Add this to your .env file as:")
        print(f"TELEGRAM_SESSION_STRING={session_string}")
        print("\nIMPORTANT: Keep this string private and never share it with anyone!")

        choice = input(
            "\nWould you like to automatically create/update your .env file with these credentials? (y/N): "
        )
        if choice.lower() == "y":
            try:
                env_contents = []
                if os.path.exists(".env"):
                    with open(".env", "r") as file:
                        env_contents = file.readlines()

                def update_or_append(contents, key, value):
                    found = False
                    for i, line in enumerate(contents):
                        if line.startswith(f"{key}="):
                            contents[i] = f"{key}={value}\n"
                            found = True
                            break
                    if not found:
                        contents.append(f"{key}={value}\n")

                update_or_append(env_contents, "TELEGRAM_API_ID", str(API_ID))
                update_or_append(env_contents, "TELEGRAM_API_HASH", API_HASH)
                update_or_append(env_contents, "TELEGRAM_SESSION_STRING", session_string)

                with open(".env", "w") as file:
                    file.writelines(env_contents)

                os.chmod(".env", 0o600)
                print("\n.env file updated successfully and secured with 600 permissions!")
            except Exception as e:
                print(f"\nError updating .env file: {e}")
                print("Please manually add the session string to your .env file.")

        client.disconnect()

    except Exception as e:
        print(f"\nError: {e}")
        print("Failed to generate session string. Please try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
