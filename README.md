# Intelligent Exam Scheduler using Metaheuristics


# Intelligent Exam Scheduler

# 🌐 Live Demo

 Intelligent Exam Scheduler(https://intelligent-exam-scheduler.onrender.com)




A working web app that generates a clash-free exam timetable automatically.
The "AI" is a **Genetic Algorithm** (a metaheuristic search technique) that
evolves thousands of candidate timetables and converges on one with zero
student clashes, zero room double-bookings, no seating overflow, and no
invigilator conflicts.

## What it does

1. You enter your institution's data: courses, students (+ which courses
   each is enrolled in), rooms, faculty/invigilators, and available exam
   time slots.
2. You click **Generate** — the Genetic Algorithm searches for the best
   possible assignment of `course → (time slot, room, invigilator)`.
3. You get a finished, clash-free timetable, plus a per-student lookup
   (useful for hall tickets).

Everything scheduling-related — which slot, which room, which invigilator —
is decided by the algorithm, not entered manually.

## Tech stack

- **Backend:** Python + Flask
- **Database:** SQLite (single file, zero setup)
- **AI engine:** custom Genetic Algorithm (`scheduler.py`) — pure Python,
  no external ML libraries required
- **Frontend:** server-rendered HTML/CSS/JS (no build step)

## Running it locally

```bash
# 1. Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

Open **http://localhost:5000** in your browser. The database is created
automatically on first run at `data/scheduler.db`, pre-loaded with a small
demo dataset (10 courses, 60 students, 6 rooms, 7 faculty, 6 time slots) so
you can see the scheduler work immediately. Delete `data/scheduler.db` and
restart the app any time to reset.

## How the algorithm works (`scheduler.py`)

Exam timetabling is NP-hard — there's no fast exact algorithm for real
institution sizes, so a metaheuristic searches the solution space
intelligently instead of checking every possibility.

- **Chromosome:** one gene per course = `(time_slot, room, invigilator)`
- **Hard constraints** (must reach zero):
  - two courses that share an enrolled student in the same time slot
  - two exams in the same room at the same time
  - more students enrolled than a room's seating capacity
  - one invigilator assigned to two rooms in the same slot
  - an invigilator exceeding their max duty count
- **Soft constraints** (improve quality once feasible):
  - a student having more than one exam on the same calendar day
  - back-to-back exams with no gap
- **Search:** tournament selection, uniform crossover, gene-level mutation,
  elitism, running for up to 300 generations or until conflicts hit zero
  (usually much sooner on realistic inputs).

You can tune `population_size` and `generations` from the Generate page's
API call in `templates/generate.html` if you have a much larger dataset.

## Project structure

```
exam_scheduler/
├── app.py                 # Flask routes (CRUD + generation API)
├── scheduler.py            # Genetic Algorithm engine
├── db.py                   # SQLite schema + demo seed data
├── requirements.txt
├── data/
│   └── scheduler.db        # created automatically
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── courses.html
    ├── students.html
    ├── rooms.html
    ├── faculty.html
    ├── timeslots.html
    ├── generate.html
    ├── timetable.html
    └── student_timetable.html
```

## Deploying for real institutional use

For production, at minimum:
1. Set a strong, random `app.secret_key` in `app.py` (via an environment
   variable, not hardcoded).
2. Run behind a real WSGI server (e.g. `gunicorn app:app`) instead of
   Flask's dev server, and put it behind a reverse proxy (nginx) with HTTPS.
3. Consider migrating from SQLite to PostgreSQL/MySQL if you expect many
   concurrent registrar staff editing data at once.
4. Add authentication/login for the admin pages before exposing this
   beyond a trusted internal network — the current version has no login,
   since it was scoped as an internal registrar tool.

## Extending it

Natural next additions: room-splitting for very large classes (one course
across multiple rooms), seating-plan generation within a room, email/SMS
notification of students once a schedule is finalized, an "undo last
generation" / version history for schedule runs, and CSV import for bulk
course/student upload.
