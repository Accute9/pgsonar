import random
import datetime
import statistics
import os
from dotenv import load_dotenv
import psycopg2

random.seed(42)  # reproducible
TODAY = datetime.date.today()
load_dotenv()  

def get_connection():
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    return psycopg2.connect(SUPABASE_DB_URL)


def seed_users(cur):
    """~60 users signing up steadily over 90 days, plus one planted anomaly:
    a bot-like burst of 25 signups on a single day."""
    rows = []
    for i in range(60):
        d = TODAY - datetime.timedelta(days=random.randint(0, 89))
        rows.append((f"user{i}@example.com", d))
 
    spike_date = TODAY - datetime.timedelta(days=30)
    for i in range(25):
        rows.append((f"spike_user{i}@example.com", spike_date))
 
    cur.executemany("INSERT INTO users (email, signup_date) VALUES (%s, %s);", rows)
    cur.execute("SELECT id FROM users ORDER BY id;")
    user_ids = [row[0] for row in cur.fetchall()]
    return spike_date, user_ids


def seed_orders(cur, user_ids):
    """300 normal orders (amount ~ N(45, 15)), spread evenly over 90 days,
    plus two planted anomalies:
        - 6 orders with wildly high amounts (price/fraud outliers)
        - 35 orders all on one day (volume spike)
    """
    normal_rows = []
    for _ in range(300):
        user_id = random.choice(user_ids)
        amount = round(max(3, random.gauss(45, 15)), 2)
        ts = datetime.datetime.now() - datetime.timedelta(
            days=random.randint(0, 89), hours=random.randint(0, 23)
        )
        normal_rows.append((user_id, amount, ts))

    outlier_amounts = [round(random.uniform(5000, 9000), 2) for _ in range(6)]
    outlier_rows = []
    for amt in outlier_amounts:
        user_id = random.choice(user_ids)
        ts = datetime.datetime.now() - datetime.timedelta(days=random.randint(0, 89))
        outlier_rows.append((user_id, amt, ts))

    spike_date = TODAY - datetime.timedelta(days=7)
    volume_spike_rows = []
    for _ in range(35):
        user_id = random.choice(user_ids)
        amount = round(max(3, random.gauss(45, 15)), 2)
        ts = datetime.datetime.combine(spike_date, datetime.time(random.randint(0, 23), 0))
        volume_spike_rows.append((user_id, amount, ts))

    all_rows = normal_rows + outlier_rows + volume_spike_rows
    # executemany doesn't hand back generated ids reliably, so we look the
    # planted outliers back up by amount afterwards (see print_ground_truth).
    cur.executemany(
        "INSERT INTO orders (user_id, amount, created_at) VALUES (%s, %s, %s);",
        all_rows,
    )

    normal_amounts = [r[1] for r in normal_rows]
    return {
        "normal_mean": round(statistics.mean(normal_amounts), 2),
        "normal_stdev": round(statistics.stdev(normal_amounts), 2),
        "outlier_amounts": outlier_amounts,
        "volume_spike_date": spike_date,
        "volume_spike_count": len(volume_spike_rows),
    }

if __name__ == "__main__":
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            spike_date, user_ids = seed_users(cur)
            print(f"Seeded {len(user_ids)} users, including a spike on {spike_date}.")
            order_stats = seed_orders(cur, user_ids)
            print(
                f"Seeded 300 orders, including {len(order_stats['outlier_amounts'])} "
                f"amount outliers and a volume spike of {order_stats['volume_spike_count']} "
                f"orders on {order_stats['volume_spike_date']}."
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error seeding data: {e}")