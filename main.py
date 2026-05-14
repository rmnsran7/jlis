import os
import requests
import sqlite3
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION (via Environment Variables) ---
LAST_NAME = os.getenv("ICBC_LAST_NAME")
LICENSE_NUMBER = os.getenv("ICBC_LICENSE_NUMBER")
KEYWORD = os.getenv("ICBC_KEYWORD", "")

# --- ICBC CONSTANTS ---
APOS_ID = 133
EXAM_TYPE = "7-R-1"
EXAM_DATE = "2026-05-25"
DAYS_OF_WEEK = "[0,1,2,3,4,5,6]"
PARTS_OF_DAY = "[0,1]"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

# --- NOTIFICATION & PERSISTENCE ---
NTFY_TOPIC = "icbc-kaur-monitor"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
NOTIFY_BEFORE_DATE = "2026-07-06"
DB_PATH = Path("appointments.db")

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS poll_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at    TEXT NOT NULL,
            total_slots INTEGER NOT NULL,
            early_slots INTEGER NOT NULL,
            success      INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_run_id      INTEGER NOT NULL REFERENCES poll_runs(id),
            appointment_date TEXT NOT NULL,
            day_of_week      TEXT NOT NULL,
            start_time       TEXT NOT NULL,
            end_time         TEXT NOT NULL,
            resource_id      INTEGER,
            is_early         INTEGER NOT NULL
        )
    """)
    con.commit()
    con.close()

def save_run(polled_at, appointments, success):
    early_count = sum(
        1 for a in appointments if a["appointmentDt"]["date"] < NOTIFY_BEFORE_DATE
    ) if appointments else 0
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO poll_runs (polled_at, total_slots, early_slots, success) VALUES (?,?,?,?)",
        (polled_at, len(appointments) if appointments else 0, early_count, 1 if success else 0),
    )
    run_id = cur.lastrowid
    if appointments:
        for a in appointments:
            appt_date = a["appointmentDt"]["date"]
            cur.execute(
                """INSERT INTO slots
                   (poll_run_id, appointment_date, day_of_week, start_time, end_time, resource_id, is_early)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    run_id, appt_date, a["appointmentDt"]["dayOfWeek"],
                    a["startTm"], a["endTm"], a.get("resourceId"),
                    1 if appt_date < NOTIFY_BEFORE_DATE else 0,
                ),
            )
    con.commit()
    con.close()

def send_notification(title, message, priority="high"):
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "car,bell"},
            timeout=10,
        )
    except Exception as e:
        print(f"Notification error: {e}")

def get_bearer_token():
    url = "https://onlinebusiness.icbc.com/deas-api/v1/webLogin/webLogin"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/login",
        "Accept": "application/json, text/plain, */*",
    }
    payload = {"drvrLastName": LAST_NAME, "licenceNumber": LICENSE_NUMBER, "keyword": KEYWORD}
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            return r.headers.get("Authorization")
        return None
    except Exception as e:
        return None

def fetch_appointments(token):
    url = "https://onlinebusiness.icbc.com/deas-api/v1/web/getAvailableAppointments"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/booking",
        "Accept": "application/json, text/plain, */*",
    }
    payload = {
        "aPosID": APOS_ID, "examType": EXAM_TYPE, "examDate": EXAM_DATE,
        "prfDaysOfWeek": DAYS_OF_WEEK, "prfPartsOfDay": PARTS_OF_DAY,
        "lastName": LAST_NAME, "licenseNumber": LICENSE_NUMBER,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        return None

def main():
    init_db()
    polled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    token = get_bearer_token()
    
    if not token:
        save_run(polled_at, [], False)
        return

    appointments = fetch_appointments(token)
    save_run(polled_at, appointments or [], appointments is not None)

    if appointments:
        early = [a for a in appointments if a["appointmentDt"]["date"] < NOTIFY_BEFORE_DATE]
        if early:
            lines = "\n".join(
                f"{a['appointmentDt']['date']} ({a['appointmentDt']['dayOfWeek']}) at {a['startTm']}"
                for a in early
            )
            send_notification(
                f"ICBC Early Slot! ({len(early)} available)",
                f"Slots before {NOTIFY_BEFORE_DATE}:\n{lines}\n\nBook: onlinebusiness.icbc.com",
                priority="urgent",
            )

if __name__ == "__main__":
    main()