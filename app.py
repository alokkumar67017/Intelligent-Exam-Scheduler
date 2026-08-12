from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime
import db
from scheduler import (
    ExamSchedulerGA,
    ExamSchedulerPSO,
    ExamSchedulerDE,
    diagnose_feasibility,
    diagnose_schedule,
)

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"

db.init_db()
db.seed_demo_data()


def defaultdict_from_db(conn):
    from collections import defaultdict
    data = defaultdict(set)
    for row in conn.execute("SELECT student_id, course_id FROM enrollments"):
        data[row["course_id"]].add(row["student_id"])
    return data


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    conn = db.get_db()
    counts = {
        "courses": conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
        "students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "rooms": conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "faculty": conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0],
        "timeslots": conn.execute("SELECT COUNT(*) FROM timeslots").fetchone()[0],
    }
    latest_run = conn.execute(
        "SELECT * FROM schedule_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    scheduled_exams = conn.execute(
        "SELECT COUNT(*) FROM exams WHERE timeslot_id IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return render_template(
        "dashboard.html",
        counts=counts,
        latest_run=latest_run,
        scheduled_exams=scheduled_exams,
    )


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------
@app.route("/courses", methods=["GET", "POST"])
def courses():
    conn = db.get_db()
    if request.method == "POST":
        code = request.form["code"].strip().upper()
        name = request.form["name"].strip()
        semester = request.form.get("semester", "").strip()
        duration = int(request.form.get("duration_minutes") or 180)
        try:
            conn.execute(
                """
                INSERT INTO courses
                (code, name, semester, duration_minutes)
                VALUES (?, ?, ?, ?)
                """,
                (code, name, semester, duration),
            )
            conn.commit()
            flash(f"Course {code} added successfully.", "success")
        except Exception as e:
            flash(f"Could not add course: {e}", "error")
        conn.close()
        return redirect(url_for("courses"))

    rows = conn.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM enrollments e WHERE e.course_id = c.id) AS enrolled
        FROM courses c
        ORDER BY c.code
        """
    ).fetchall()
    conn.close()
    return render_template("courses.html", courses=rows, edit_item=None)


@app.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
def edit_course(course_id):
    conn = db.get_db()
    if request.method == "POST":
        code = request.form["code"].strip().upper()
        name = request.form["name"].strip()
        semester = request.form.get("semester", "").strip()
        duration = int(request.form.get("duration_minutes") or 180)
        try:
            conn.execute(
                """
                UPDATE courses
                SET code=?, name=?, semester=?, duration_minutes=?
                WHERE id=?
                """,
                (code, name, semester, duration, course_id),
            )
            conn.commit()
            flash("Course updated successfully.", "success")
        except Exception as e:
            flash(f"Could not update course: {e}", "error")
        conn.close()
        return redirect(url_for("courses"))

    edit_item = conn.execute(
        "SELECT * FROM courses WHERE id=?", (course_id,)
    ).fetchone()

    rows = conn.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM enrollments e WHERE e.course_id = c.id) AS enrolled
        FROM courses c
        ORDER BY c.code
        """
    ).fetchall()
    conn.close()
    return render_template("courses.html", courses=rows, edit_item=edit_item)


@app.route("/courses/<int:course_id>/delete", methods=["POST"])
def delete_course(course_id):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM courses WHERE id=?", (course_id,))
        conn.commit()
        flash("Course removed successfully.", "success")
    except Exception as e:
        flash(f"Could not remove course: {e}", "error")
    conn.close()
    return redirect(url_for("courses"))


# ---------------------------------------------------------------------------
# Students + Enrollments
# ---------------------------------------------------------------------------
@app.route("/students", methods=["GET", "POST"])
def students():
    conn = db.get_db()
    if request.method == "POST":
        roll_no = request.form["roll_no"].strip().upper()
        name = request.form["name"].strip()
        semester = request.form.get("semester", "").strip()
        course_ids = request.form.getlist("course_ids")
        try:
            cur = conn.execute(
                "INSERT INTO students (roll_no, name, semester) VALUES (?,?,?)",
                (roll_no, name, semester),
            )
            sid = cur.lastrowid
            for cid in course_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?,?)",
                    (sid, cid),
                )
            conn.commit()
            flash(f"Student {roll_no} added with {len(course_ids)} course(s).", "success")
        except Exception as e:
            flash(f"Could not add student: {e}", "error")
        conn.close()
        return redirect(url_for("students"))

    rows = conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM enrollments e WHERE e.student_id = s.id) AS n_courses
        FROM students s
        ORDER BY s.roll_no
        """
    ).fetchall()
    all_courses = conn.execute("SELECT * FROM courses ORDER BY code").fetchall()
    conn.close()
    return render_template(
        "students.html", students=rows, all_courses=all_courses, edit_item=None
    )


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
def edit_student(student_id):
    conn = db.get_db()
    if request.method == "POST":
        roll_no = request.form["roll_no"].strip().upper()
        name = request.form["name"].strip()
        semester = request.form.get("semester", "").strip()
        course_ids = request.form.getlist("course_ids")
        try:
            conn.execute(
                "UPDATE students SET roll_no=?, name=?, semester=? WHERE id=?",
                (roll_no, name, semester, student_id),
            )
            conn.execute("DELETE FROM enrollments WHERE student_id=?", (student_id,))
            for cid in course_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?,?)",
                    (student_id, cid),
                )
            conn.commit()
            flash(f"Student {roll_no} updated.", "success")
        except Exception as e:
            flash(f"Could not update student: {e}", "error")
        conn.close()
        return redirect(url_for("students"))

    edit_item = conn.execute(
        "SELECT * FROM students WHERE id=?", (student_id,)
    ).fetchone()
    enrolled_rows = conn.execute(
        "SELECT course_id FROM enrollments WHERE student_id=?", (student_id,)
    ).fetchall()
    enrolled_ids = [r["course_id"] for r in enrolled_rows]

    rows = conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM enrollments e WHERE e.student_id = s.id) AS n_courses
        FROM students s
        ORDER BY s.roll_no
        """
    ).fetchall()
    all_courses = conn.execute("SELECT * FROM courses ORDER BY code").fetchall()
    conn.close()
    return render_template(
        "students.html",
        students=rows,
        all_courses=all_courses,
        edit_item=edit_item,
        enrolled_ids=enrolled_ids,
    )


@app.route("/students/<int:student_id>/delete", methods=["POST"])
def delete_student(student_id):
    conn = db.get_db()
    conn.execute("DELETE FROM students WHERE id=?", (student_id,))
    conn.commit()
    conn.close()
    flash("Student removed.", "success")
    return redirect(url_for("students"))


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------
@app.route("/rooms", methods=["GET", "POST"])
def rooms():
    conn = db.get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        capacity = int(request.form["capacity"])
        try:
            conn.execute(
                "INSERT INTO rooms (name, capacity) VALUES (?,?)", (name, capacity)
            )
            conn.commit()
            flash(f"Room {name} added.", "success")
        except Exception as e:
            flash(f"Could not add room: {e}", "error")
        conn.close()
        return redirect(url_for("rooms"))

    

    rows = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
    conn.close()
    return render_template("rooms.html", rooms=rows, edit_item=None)


@app.route("/rooms/<int:room_id>/edit", methods=["GET", "POST"])
def edit_room(room_id):
    conn = db.get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        capacity = int(request.form["capacity"])
        try:
            conn.execute(
                "UPDATE rooms SET name=?, capacity=? WHERE id=?",
                (name, capacity, room_id),
            )
            conn.commit()
            flash(f"Room {name} updated.", "success")
        except Exception as e:
            flash(f"Could not update room: {e}", "error")
        conn.close()
        return redirect(url_for("rooms"))

    edit_item = conn.execute(
        "SELECT * FROM rooms WHERE id=?", (room_id,)
    ).fetchone()
    rows = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
    conn.close()
    return render_template("rooms.html", rooms=rows, edit_item=edit_item)


@app.route("/rooms/<int:room_id>/delete", methods=["POST"])
def delete_room(room_id):
    conn = db.get_db()
    conn.execute("DELETE FROM rooms WHERE id=?", (room_id,))
    conn.commit()
    conn.close()
    flash("Room removed.", "success")
    return redirect(url_for("rooms"))

@app.route("/rooms/<int:room_id>/toggle", methods=["POST"])
def toggle_room(room_id):
    conn = db.get_db()
    row = conn.execute("SELECT is_active FROM rooms WHERE id=?", (room_id,)).fetchone()
    if row is not None:
        # handle both old rows (None) and new rows
        current = row["is_active"] if row["is_active"] is not None else 1
        new_val = 0 if current else 1
        conn.execute("UPDATE rooms SET is_active=? WHERE id=?", (new_val, room_id))
        conn.commit()
    conn.close()
    return redirect(url_for("rooms"))


# ---------------------------------------------------------------------------
# Faculty
# ---------------------------------------------------------------------------
@app.route("/faculty", methods=["GET", "POST"])
def faculty():
    conn = db.get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        department = request.form.get("department", "").strip()
        max_inv = int(request.form.get("max_invigilations") or 10)
        try:
            conn.execute(
                "INSERT INTO faculty (name, department, max_invigilations) VALUES (?,?,?)",
                (name, department, max_inv),
            )
            conn.commit()
            flash(f"Faculty {name} added.", "success")
        except Exception as e:
            flash(f"Could not add faculty: {e}", "error")
        conn.close()
        return redirect(url_for("faculty"))

    rows = conn.execute("SELECT * FROM faculty ORDER BY name").fetchall()
    conn.close()
    return render_template("faculty.html", faculty=rows, edit_item=None)


@app.route("/faculty/<int:faculty_id>/edit", methods=["GET", "POST"])
def edit_faculty(faculty_id):
    conn = db.get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        department = request.form.get("department", "").strip()
        max_inv = int(request.form.get("max_invigilations") or 10)
        try:
            conn.execute(
                "UPDATE faculty SET name=?, department=?, max_invigilations=? WHERE id=?",
                (name, department, max_inv, faculty_id),
            )
            conn.commit()
            flash(f"Faculty {name} updated.", "success")
        except Exception as e:
            flash(f"Could not update faculty: {e}", "error")
        conn.close()
        return redirect(url_for("faculty"))

    edit_item = conn.execute(
        "SELECT * FROM faculty WHERE id=?", (faculty_id,)
    ).fetchone()
    rows = conn.execute("SELECT * FROM faculty ORDER BY name").fetchall()
    conn.close()
    return render_template("faculty.html", faculty=rows, edit_item=edit_item)


@app.route("/faculty/<int:faculty_id>/delete", methods=["POST"])
def delete_faculty(faculty_id):
    conn = db.get_db()
    conn.execute("DELETE FROM faculty WHERE id=?", (faculty_id,))
    conn.commit()
    conn.close()
    flash("Faculty removed.", "success")
    return redirect(url_for("faculty"))

@app.route("/faculty/<int:faculty_id>/toggle", methods=["POST"])
def toggle_faculty(faculty_id):
    conn = db.get_db()
    row = conn.execute("SELECT is_active FROM faculty WHERE id=?", (faculty_id,)).fetchone()
    if row is not None:
        current = row["is_active"] if row["is_active"] is not None else 1
        new_val = 0 if current else 1
        conn.execute("UPDATE faculty SET is_active=? WHERE id=?", (new_val, faculty_id))
        conn.commit()
    conn.close()
    return redirect(url_for("faculty"))


# ---------------------------------------------------------------------------
# Timeslots
# ---------------------------------------------------------------------------
@app.route("/timeslots", methods=["GET", "POST"])
def timeslots():
    conn = db.get_db()
    if request.method == "POST":
        exam_date = request.form["exam_date"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]
        label = request.form.get("label", "").strip()
        try:
            conn.execute(
                "INSERT INTO timeslots (exam_date, start_time, end_time, label) VALUES (?,?,?,?)",
                (exam_date, start_time, end_time, label),
            )
            conn.commit()
            flash("Time slot added.", "success")
        except Exception as e:
            flash(f"Could not add time slot: {e}", "error")
        conn.close()
        return redirect(url_for("timeslots"))

    rows = conn.execute(
        "SELECT * FROM timeslots ORDER BY exam_date, start_time"
    ).fetchall()
    conn.close()
    return render_template("timeslots.html", timeslots=rows, edit_item=None)


@app.route("/timeslots/<int:ts_id>/edit", methods=["GET", "POST"])
def edit_timeslot(ts_id):
    conn = db.get_db()
    if request.method == "POST":
        exam_date = request.form["exam_date"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]
        label = request.form.get("label", "").strip()
        try:
            conn.execute(
                "UPDATE timeslots SET exam_date=?, start_time=?, end_time=?, label=? WHERE id=?",
                (exam_date, start_time, end_time, label, ts_id),
            )
            conn.commit()
            flash("Time slot updated.", "success")
        except Exception as e:
            flash(f"Could not update time slot: {e}", "error")
        conn.close()
        return redirect(url_for("timeslots"))

    edit_item = conn.execute(
        "SELECT * FROM timeslots WHERE id=?", (ts_id,)
    ).fetchone()
    rows = conn.execute(
        "SELECT * FROM timeslots ORDER BY exam_date, start_time"
    ).fetchall()
    conn.close()
    return render_template("timeslots.html", timeslots=rows, edit_item=edit_item)


@app.route("/timeslots/<int:ts_id>/delete", methods=["POST"])
def delete_timeslot(ts_id):
    conn = db.get_db()
    conn.execute("DELETE FROM timeslots WHERE id=?", (ts_id,))
    conn.commit()
    conn.close()
    flash("Time slot removed.", "success")
    return redirect(url_for("timeslots"))


# ---------------------------------------------------------------------------
# AI Schedule generation
# ---------------------------------------------------------------------------
@app.route("/generate")
def generate_page():
    conn = db.get_db()
    counts = {
        "courses": conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
        "rooms": conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "faculty": conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0],
        "timeslots": conn.execute("SELECT COUNT(*) FROM timeslots").fetchone()[0],
    }
    conn.close()
    return render_template("generate.html", counts=counts)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Runs the chosen algorithm and returns timetable + diagnostics.
       Optionally filters by semesters so only those semesters are scheduled.
    """
    conn = db.get_db()
    params = request.get_json(silent=True) or {}
    
    semesters = params.get("semesters") or []          # list like ["3","5","7"]
    # clean & convert to strings
    semesters = [str(s).strip() for s in semesters if str(s).strip()]

    # ---------- Load data (optionally filtered by semester) ----------
    if semesters:
        placeholders = ",".join("?" * len(semesters))
        courses = [dict(r) for r in conn.execute(
            f"SELECT * FROM courses WHERE semester IN ({placeholders})",
            semesters
        ).fetchall()]
        
        student_rows = conn.execute(
            f"SELECT id FROM students WHERE semester IN ({placeholders})",
            semesters
        ).fetchall()
        
        student_ids = [s["id"] for s in student_rows]
        
        if student_ids:
            ph2 = ",".join("?" * len(student_ids))
            enrollment_rows = conn.execute(
                f"SELECT student_id, course_id FROM enrollments WHERE student_id IN ({ph2})",
                student_ids
            ).fetchall()
        else:
            enrollment_rows = []
    else:
        # All data
        courses = [dict(r) for r in conn.execute("SELECT * FROM courses").fetchall()]
        enrollment_rows = conn.execute(
            "SELECT student_id, course_id FROM enrollments"
        ).fetchall()

    rooms = [dict(r) for r in conn.execute(
        "SELECT * FROM rooms WHERE COALESCE(is_active, 1) = 1"
    ).fetchall()]

    faculty = [dict(r) for r in conn.execute(
        "SELECT * FROM faculty WHERE COALESCE(is_active, 1) = 1"
    ).fetchall()]

    timeslots = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM timeslots ORDER BY exam_date, start_time"
        ).fetchall()
    ]

    if not (courses and rooms and faculty and timeslots):
        conn.close()
        return jsonify(
            {
                "error": "Add at least one course, room, faculty member, and time slot first."
                + (f" (No data found for semesters '{', '.join(semesters)}') " if semesters else "")
            }
        ), 400

    # Build students_by_course from the (possibly filtered) enrollments
    from collections import defaultdict
    students_by_course = defaultdict(set)
    for row in enrollment_rows:
        students_by_course[row["course_id"]].add(row["student_id"])

    # Keep only enrollments that belong to the selected courses
    course_ids_set = {c["id"] for c in courses}
    students_by_course = {
        cid: students
        for cid, students in students_by_course.items()
        if cid in course_ids_set
    }

    pre_diagnosis = diagnose_feasibility(
        courses, students_by_course, rooms, faculty, timeslots
    )

    algorithm_choice = params.get("algorithm", "GA")

    algo_args = {
        "courses": courses,
        "students_by_course": students_by_course,
        "rooms": rooms,
        "faculty": faculty,
        "timeslots": timeslots,
        "population_size": int(params.get("population_size", 80)),
        "generations": int(params.get("generations", 400)),
    }

    if algorithm_choice == "PSO":
        scheduler = ExamSchedulerPSO(**algo_args)
    elif algorithm_choice == "DE":
        scheduler = ExamSchedulerDE(**algo_args)
    else:
        scheduler = ExamSchedulerGA(**algo_args)

    result = scheduler.run()
    hard_conflicts = result["hard_conflicts"]
    final_cost = result["final_cost"]
    conflict_details = result.get("conflict_details") or {}

    post_diagnosis = diagnose_schedule(
        conflict_details,
        hard_conflicts,
        courses=courses,
        faculty=faculty,
        rooms=rooms,
        timeslots=timeslots,
    )

    diagnosis = {
        "feasible": hard_conflicts == 0,
        "problems": post_diagnosis["problems"],
        "suggestions": post_diagnosis["suggestions"],
        "stats": pre_diagnosis.get("stats", {}),
    }
    if hard_conflicts > 0:
        seen = set(diagnosis["suggestions"])
        for s in pre_diagnosis.get("suggestions", []):
            if s not in seen:
                diagnosis["suggestions"].append(s)
                seen.add(s)
        for p in pre_diagnosis.get("problems", []):
            if p not in diagnosis["problems"]:
                diagnosis["problems"].append(p)

    if hard_conflicts == 0:
        accuracy_percentage = 100.0
        status = "clash-free"
    else:
        accuracy_percentage = max(0.0, 100.0 - hard_conflicts * 4.0)
        status = "has-clashes"

    # ---------- Persist ----------
    conn.execute("DELETE FROM exams")

    for course_id, placements in result["assignment"].items():
        if not isinstance(placements, list):
            placements = [placements]
        for item in placements:
            ts_id, room_id, fac_id = item
            conn.execute(
                """
                INSERT INTO exams
                (course_id, timeslot_id, room_id, faculty_id, run_id)
                VALUES (?,?,?,?,?)
                """,
                (course_id, ts_id, room_id, fac_id, result["run_id"]),
            )

    conn.execute(
        """
        INSERT INTO schedule_runs
        (id, created_at, generations_run, final_conflicts, final_fitness, seconds_taken)
        VALUES (?,?,?,?,?,?)
        """,
        (
            result["run_id"],
            datetime.utcnow().isoformat(),
            result.get("generations_run", 1),
            hard_conflicts,
            final_cost,
            result["seconds_taken"],
        ),
    )
    conn.commit()
    conn.close()

    return jsonify(
        {
            "run_id": result["run_id"],
            "generations_run": result.get("generations_run", 1),
            "hard_conflicts": hard_conflicts,
            "final_cost": final_cost,
            "accuracy": round(accuracy_percentage, 1),
            "status": status,
            "seconds_taken": result["seconds_taken"],
            "history": result.get("history", [])[-40:],
            "diagnosis": diagnosis,
            "message": (
                "Schedule generated successfully (clash-free)."
                if hard_conflicts == 0
                else f"Schedule generated but has {hard_conflicts} hard conflict(s). See suggestions below."
            ),
            "semester_filter": ", ".join(semesters) if semesters else "All",
        }
    )
# ---------------------------------------------------------------------------
# Timetable views
# ---------------------------------------------------------------------------
@app.route("/timetable")
def timetable():
    conn = db.get_db()
    rows = conn.execute(
        """
        SELECT e.id, c.code, c.name AS course_name,
               t.exam_date, t.start_time, t.end_time, t.label,
               r.name AS room_name, r.capacity,
               f.name AS faculty_name,
               (SELECT COUNT(*) FROM enrollments en WHERE en.course_id = c.id) AS enrolled
        FROM exams e
        JOIN courses c ON c.id = e.course_id
        LEFT JOIN timeslots t ON t.id = e.timeslot_id
        LEFT JOIN rooms r ON r.id = e.room_id
        LEFT JOIN faculty f ON f.id = e.faculty_id
        ORDER BY t.exam_date, t.start_time, c.code
        """
    ).fetchall()
    latest_run = conn.execute(
        "SELECT * FROM schedule_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return render_template("timetable.html", exams=rows, latest_run=latest_run)


@app.route("/timetable/student")
def student_timetable():
    roll_no = request.args.get("roll_no", "").strip().upper()
    conn = db.get_db()
    student = None
    exams = []
    if roll_no:
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?", (roll_no,)
        ).fetchone()
        if student:
            exams = conn.execute(
                """
                SELECT c.code, c.name AS course_name,
                       t.exam_date, t.start_time, t.end_time,
                       r.name AS room_name, f.name AS faculty_name
                FROM enrollments en
                JOIN courses c ON c.id = en.course_id
                JOIN exams e ON e.course_id = c.id
                LEFT JOIN timeslots t ON t.id = e.timeslot_id
                LEFT JOIN rooms r ON r.id = e.room_id
                LEFT JOIN faculty f ON f.id = e.faculty_id
                WHERE en.student_id = ?
                ORDER BY t.exam_date, t.start_time
                """,
                (student["id"],),
            ).fetchall()
    conn.close()
    return render_template(
        "student_timetable.html", roll_no=roll_no, student=student, exams=exams
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)