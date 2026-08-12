"""
db.py
Database layer for the Intelligent Exam Scheduler.
Uses plain sqlite3 (no ORM) so the project runs anywhere Python + Flask run,
with zero extra dependencies beyond requirements.txt.
"""
import sqlite3
import os
import random

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "scheduler.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    semester TEXT,
    duration_minutes INTEGER NOT NULL DEFAULT 180
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    semester TEXT
);

CREATE TABLE IF NOT EXISTS enrollments (
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    PRIMARY KEY (student_id, course_id),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    capacity INTEGER NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS faculty (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department TEXT,
    max_invigilations INTEGER NOT NULL DEFAULT 10,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS timeslots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    label TEXT
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    timeslot_id INTEGER,
    room_id INTEGER,
    faculty_id INTEGER,
    run_id TEXT,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
    FOREIGN KEY (timeslot_id) REFERENCES timeslots(id) ON DELETE SET NULL,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL,
    FOREIGN KEY (faculty_id) REFERENCES faculty(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    generations_run INTEGER,
    final_conflicts INTEGER,
    final_fitness REAL,
    seconds_taken REAL
);
"""

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    
    # Run the base schema creation
    conn.executescript(SCHEMA)
    
    # Migration: Attempt to add columns to existing tables safely
    try:
        conn.execute("ALTER TABLE rooms ADD COLUMN is_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # Column likely already exists

    try:
        conn.execute("ALTER TABLE faculty ADD COLUMN is_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # Column likely already exists

    conn.commit()
    conn.close()

def seed_demo_data():
    """
    Realistic multi-semester seed data with controlled student overlaps.
    Semesters 3,4,5,6 so the multi-select filter is useful.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM courses")
    if cur.fetchone()[0] > 0:
        conn.close()
        return

    random.seed(42)

    # ------------------------------------------------------------------
    # 1. Courses (Semesters 3-6)
    # ------------------------------------------------------------------
    branches = ["CS", "EC", "ME", "CE", "EE"]
    courses = []

    for sem in ["3", "4", "5", "6"]:
        for branch in branches:
            for i in range(1, 7):
                code = f"{branch}{sem}0{i}"
                name = f"{branch} Core Subject {i} (Sem {sem})"
                courses.append((code, name, sem, 180))

        # Two open electives per semester
        courses.append((f"OE{sem}01", f"Open Elective A (Sem {sem})", sem, 180))
        courses.append((f"OE{sem}02", f"Open Elective B (Sem {sem})", sem, 180))

    cur.executemany(
        "INSERT INTO courses (code, name, semester, duration_minutes) VALUES (?,?,?,?)",
        courses,
    )

    # ------------------------------------------------------------------
    # 2. Rooms
    # ------------------------------------------------------------------
    rooms = [
        ("Hall A",   60, 1),
        ("Hall B",   50, 1),
        ("MA201",  200, 1),
        ("MA212",   50, 1),
        ("MA220",  100, 1),
        ("MA221",   50, 1),
        ("MB202",   50, 1),
        ("MC212",  100, 1),
        ("Room 101", 40, 1),
        ("Room 102", 35, 1),
    ]
    cur.executemany(
        "INSERT INTO rooms (name, capacity, is_active) VALUES (?,?,?)",
        rooms,
    )

    # ------------------------------------------------------------------
    # 3. Faculty
    # ------------------------------------------------------------------
    faculty = []
    names = [f"Prof. {chr(65+i)}" for i in range(12)]  # Prof. A ... Prof. L
    for name in names:
        faculty.append((name, "ENG", 10, 1))

    cur.executemany(
        "INSERT INTO faculty (name, department, max_invigilations, is_active) VALUES (?,?,?,?)",
        faculty,
    )

    # ------------------------------------------------------------------
    # 4. Timeslots – 4 days × 2 = 8 slots (much better for zero clashes)
    # ------------------------------------------------------------------
    slots = []
    for i in range(4):
        date_str = f"2026-08-{9 + i}"
        slots.append((date_str, "10:00", "13:00", f"Day {i+1} Morning"))
        slots.append((date_str, "14:00", "17:00", f"Day {i+1} Afternoon"))

    cur.executemany(
        "INSERT INTO timeslots (exam_date, start_time, end_time, label) VALUES (?,?,?,?)",
        slots,
    )
    conn.commit()

    # ------------------------------------------------------------------
    # 5. Students + realistic enrollments
    # ------------------------------------------------------------------
    cur.execute("SELECT id, code, semester FROM courses")
    all_courses = cur.fetchall()

    # Build helpers
    courses_by_sem_branch = {}   # (sem, branch) → [course_ids]
    electives_by_sem = {}        # sem → [elective_ids]

    for row in all_courses:
        code = row["code"]
        sem = row["semester"]
        cid = row["id"]

        if code.startswith("OE"):
            electives_by_sem.setdefault(sem, []).append(cid)
        else:
            branch = code[:2]
            courses_by_sem_branch.setdefault((sem, branch), []).append(cid)

    for sem in ["3", "4", "5", "6"]:
        for b_idx, branch in enumerate(branches):
            # 12 students per branch per semester
            for i in range(1, 13):
                roll_no = f"S{sem}{branch}{i:02d}"
                name = f"Student {sem}-{branch}-{i}"
                cur.execute(
                    "INSERT INTO students (roll_no, name, semester) VALUES (?,?,?)",
                    (roll_no, name, sem),
                )
                sid = cur.lastrowid

                # Core subjects of own branch (take 5 out of 6)
                core = courses_by_sem_branch.get((sem, branch), [])
                if core:
                    chosen_core = random.sample(core, min(5, len(core)))
                    for cid in chosen_core:
                        cur.execute(
                            "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?,?)",
                            (sid, cid),
                        )

                # 30% chance to take one open elective
                if random.random() < 0.30:
                    elecs = electives_by_sem.get(sem, [])
                    if elecs:
                        cid = random.choice(elecs)
                        cur.execute(
                            "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?,?)",
                            (sid, cid),
                        )

    conn.commit()
    conn.close()
    print("Improved demo data seeded (Semesters 3-6, realistic enrollments, 8 timeslots).")