import time
import threading
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from transformers import pipeline
import torch
from plyer import notification
from playsound import playsound
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button
from tkcalendar import DateEntry
import asyncio
import pyttsx3
import os.path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langdetect import detect
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# ---- Telegram API Credentials ----
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")

# ---- Load Intent Classifier ----
device = 0 if torch.cuda.is_available() else -1
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=device)
labels = ["reminder", "deadline", "question", "event update", "greeting", "casual"]

# ---- Tkinter Setup ----
root = tk.Tk()
root.withdraw()

# ---- Voice Engine Setup ----
engine = pyttsx3.init()
engine.setProperty('rate', 170)

#------- Database connection function--------
def log_reminder_to_db(message, sender_name):
    try:
        # Connect to your PostgreSQL database
        conn = psycopg2.connect(
            dbname= os.getenv("DB_NAME"),  # Replace with your actual database name
            user=os.getenv("DB_USER"),  # Replace with your database username
            password=os.getenv("DB_PASSWORD"),  # Replace with your password
            host="localhost",
            port="5432"
        )
        
        # Create a cursor object
        cur = conn.cursor()

        # Get the current timestamp
        current_time = datetime.now()

        # Insert the reminder into the database
        cur.execute("""
            INSERT INTO reminders (message, sender_name, remind_at) 
            VALUES (%s, %s, %s)
        """, (message, sender_name, current_time))

        # Commit the transaction
        conn.commit()

        # Close the cursor and connection
        cur.close()
        conn.close()

        print(f"✅ Reminder from {sender_name} added to the database.")

    except Exception as e:
        print(f"⚠️ Error logging reminder to database: {e}")


# ---- Google Calendar Setup ----
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    creds = None
    # Get file paths from environment variables
    credentials_path = os.getenv('GOOGLE_API_CREDENTIALS_PATH')
    token_path = os.getenv('GOOGLE_API_TOKEN_PATH')
    
    # Check if token.json exists
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials to token.json
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    
    return build('calendar', 'v3', credentials=creds)

# ---- Reminder Logger ----
def log_reminder(message):
    # Get the current date to name the log file (e.g., log_2025-04-14.txt)
    log_date = datetime.now().strftime("%Y-%m-%d")
    log_filename = f"log_{log_date}.txt"

    # Get the current time for the log entry
    log_time = datetime.now().strftime("%H:%M:%S")

    # Format the log entry
    log_entry = f"[{log_time}] {message}\n"

    # Write the log entry to the specific daily log file
    with open(log_filename, "a", encoding="utf-8") as log_file:
        log_file.write(log_entry)

# ---- Custom Calendar-Based Reminder Popup ----
def schedule_reminder(message):
    def open_schedule_dialog():
        win = Toplevel()
        win.title("Schedule Reminder")

        Label(win, text="Select Date:").grid(row=0, column=0, padx=10, pady=5)
        cal = DateEntry(win, date_pattern='dd/mm/yyyy', width=15)
        cal.grid(row=0, column=1, padx=10, pady=5)

        Label(win, text="Enter Time (hh:mm AM/PM):").grid(row=1, column=0, padx=10, pady=5)
        time_entry = Entry(win)
        time_entry.grid(row=1, column=1, padx=10, pady=5)

        def submit():
            try:
                date_part = cal.get_date()
                time_str = time_entry.get()
                time_part = datetime.strptime(time_str, "%I:%M %p").time()

                scheduled_datetime = datetime.combine(date_part, time_part)
                now = datetime.now()
                if scheduled_datetime < now:
                    messagebox.showerror("Error", "You cannot schedule reminders in the past.")
                    return

                seconds_until = (scheduled_datetime - now).total_seconds()
                print(f"⏳ Scheduled for {scheduled_datetime.strftime('%d/%m/%Y %I:%M %p')} ({int(seconds_until)}s from now)")

                threading.Timer(seconds_until, lambda: show_reminder(message, scheduled_datetime)).start()

                try:
                    service = get_calendar_service()
                    event = {
                        'summary': 'Telegram Reminder',
                        'description': message,
                        'start': {'dateTime': scheduled_datetime.isoformat(), 'timeZone': 'Asia/Kolkata'},
                        'end': {'dateTime': scheduled_datetime.isoformat(), 'timeZone': 'Asia/Kolkata'},
                    }
                    service.events().insert(calendarId='primary', body=event).execute()
                    print("✅ Added to Google Calendar.")
                except Exception as e:
                    print(f"❌ Google Calendar Error: {e}")

                win.destroy()

            except Exception as e:
                messagebox.showerror("Invalid Input", "Please enter time in hh:mm AM/PM format.")

        Button(win, text="Schedule", command=submit).grid(row=2, columnspan=2, pady=10)

    root.after(0, open_schedule_dialog)

# ---- Email Setup ----
def send_email_notification(subject, message):
    sender_email = os.getenv("SENDER_EMAIL")
    receiver_email = "geshnastar@gmail.com"
    password = os.getenv("SENDER_PASSWORD")

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(message, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

# ---- Reminder Popup Logic ----
def show_reminder(message, scheduled_time):
    def reminder_action():
        print("⏰ Reminder triggered!")
        log_reminder(message)
        #log_reminder_to_db(message, "Telegram User") 

        engine.say("Reminder alert!")
        engine.runAndWait()

        notification.notify(
            title="🔔 Reminder!",
            message=message,
            timeout=10
        )

        try:
            playsound("SystemExit.wav")
        except:
            pass

        original_time = datetime.now().strftime("%d/%m/%Y %I:%M %p")
        if scheduled_time:
            send_email_notification(
                "Reminder Notification",
                f"Reminder triggered at {scheduled_time.strftime('%d/%m/%Y %I:%M %p')}:\n\n{message}"
            )

        def ask_reschedule():
            reschedule = messagebox.askyesno("Reschedule Reminder", "⏰ Do you want to reschedule this reminder?")
            if reschedule:
                schedule_reminder(message)
                send_email_notification(
                    "Reminder Rescheduled",
                    f"Reminder rescheduled at {original_time}:\n\n{message}"
                )

        root.after(0, ask_reschedule)

    threading.Thread(target=reminder_action).start()

# ---- Telegram Client Initialization ----
client = TelegramClient("intent_reader", api_id, api_hash)

# ---- Handle New Telegram Messages ----
@client.on(events.NewMessage(incoming=True))
async def handle_new_message(event):
    try:
        sender = await event.get_sender()
        name = (f"{sender.first_name} {getattr(sender, 'last_name', '')}").strip() if hasattr(sender, 'first_name') else getattr(sender, 'title', 'Unknown')
    except Exception:
        name = "Unknown"

    msg = event.raw_text.strip()
    if msg:
        print(f"\n📥 Message from {name}: {msg}")

        result = classifier(msg, labels)
        top_intent = result['labels'][0]
        print(f"🎯 Detected intent: {top_intent}")

        if top_intent in ['reminder', 'deadline', 'event update']:
            msg_display = ' '.join(msg.split()[:30])
            formatted_msg = f"{msg_display}\n\n—from {name}"

            show_reminder(formatted_msg, None)
            


            log_reminder_to_db(formatted_msg, name)


            # 🚫 Only allow manual reply for specific intents
            if top_intent in ['reminder', 'deadline', 'event update']:
                user_reply = input("\n✉️ Type a reply (or press Enter to skip): ").strip()
                if user_reply:
                    await event.reply(user_reply)
                    print("✅ Reply sent!")
                else:
                    print("⏭️ Skipped replying.")

# ---- Telegram Listener ----
async def telegram_main():
    await client.start()
    print("✅ Connected! Listening to your messages...")
    await client.run_until_disconnected()

def run_telegram():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_main())

# ---- Start Everything ----
threading.Thread(target=run_telegram, daemon=True).start()

root.mainloop()
