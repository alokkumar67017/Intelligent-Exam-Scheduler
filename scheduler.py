"""
scheduler.py — Exam timetable metaheuristics (from scratch)

HARD constraints:
  - No two courses that share a student in the same timeslot
  - Room capacity never exceeded (courses may SHARE a room if seats allow)
  - One faculty cannot invigilate two rooms at the same time
  - Faculty max_invigilations not exceeded

SOFT constraints:
  - Prefer not putting shared-student courses on the same day
  - Prefer not putting them in consecutive slots

Representation:
  Chromosome = list of timeslot_id, one per course.
  Rooms + faculty are assigned by a deterministic packer.
  Multiple courses can share one room; large courses are split
  across rooms using free seats.
"""

from __future__ import annotations

import random
import time
import uuid
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Tuple


P_STUDENT_CLASH = 1000
P_ROOM_OVERFLOW = 800
P_FAC_DOUBLE = 700
P_FAC_OVERLOAD = 200
P_SAME_DAY = 5
P_BACK_TO_BACK = 3


class ExamSchedulerBase:
    def __init__(
        self,
        courses: List[dict],
        students_by_course: Dict[int, set],
        rooms: List[dict],
        faculty: List[dict],
        timeslots: List[dict],
        population_size: int = 80,
        generations: int = 400,
        seed: Optional[int] = None,
    ):
        self.courses = courses
        self.course_ids: List[int] = [c["id"] for c in courses]
        self.n = len(self.course_ids)
        self.students_by_course = students_by_course
        self.rooms = rooms
        self.faculty = faculty
        self.timeslots = timeslots

        self.timeslot_ids: List[int] = [t["id"] for t in timeslots]
        self.room_ids: List[int] = [r["id"] for r in rooms]
        self.room_cap: Dict[int, int] = {r["id"]: int(r["capacity"]) for r in rooms}
        self.faculty_ids: List[int] = [f["id"] for f in faculty]
        self.faculty_max: Dict[int, int] = {
            f["id"]: int(f.get("max_invigilations") or 999) for f in faculty
        }
        self.ts_day: Dict[int, str] = {t["id"]: t["exam_date"] for t in timeslots}
        self.ts_order: Dict[int, int] = {t["id"]: i for i, t in enumerate(timeslots)}

        self.code: Dict[int, str] = {
            c["id"]: c.get("code", str(c["id"])) for c in courses
        }
        self.room_name: Dict[int, str] = {
            r["id"]: r.get("name", str(r["id"])) for r in rooms
        }
        self.fac_name: Dict[int, str] = {
            f["id"]: f.get("name", str(f["id"])) for f in faculty
        }

        self.enrolled: Dict[int, int] = {
            cid: len(students_by_course.get(cid, ())) for cid in self.course_ids
        }

        self.shared: Dict[Tuple[int, int], int] = {}
        for i in range(self.n):
            for j in range(i + 1, self.n):
                a, b = self.course_ids[i], self.course_ids[j]
                inter = students_by_course.get(a, set()) & students_by_course.get(b, set())
                if inter:
                    self.shared[(a, b)] = len(inter)

        self.neighbours: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for (a, b), cnt in self.shared.items():
            self.neighbours[a].append((b, cnt))
            self.neighbours[b].append((a, cnt))

        self.rooms_desc = sorted(
            self.room_ids, key=lambda rid: self.room_cap[rid], reverse=True
        )

        self.population_size = max(20, population_size)
        self.generations = max(1, generations)
        self.rng = random.Random(seed)

    def _random_chrom(self) -> List[int]:
        return [self.rng.choice(self.timeslot_ids) for _ in range(self.n)]

    def _smart_chrom(self) -> List[int]:
        chrom: List[Optional[int]] = [None] * self.n
        load = defaultdict(int)
        order = sorted(
            range(self.n),
            key=lambda i: (
                self.enrolled[self.course_ids[i]]
                + sum(c for _, c in self.neighbours[self.course_ids[i]]),
            ),
            reverse=True,
        )
        for i in order:
            cid = self.course_ids[i]
            best_ts, best_score = None, None
            for ts in self.timeslot_ids:
                clash = 0
                for other, cnt in self.neighbours[cid]:
                    oi = self.course_ids.index(other)
                    if chrom[oi] == ts:
                        clash += cnt
                score = clash * 10000 + load[ts]
                if best_score is None or score < best_score:
                    best_score, best_ts = score, ts
            chrom[i] = best_ts
            load[best_ts] += 1
        return [t if t is not None else self.timeslot_ids[0] for t in chrom]

    def _pack(
        self, chrom: List[int]
    ) -> Tuple[Dict[int, List[Tuple[int, int, int]]], int, dict]:
        """
        Assign rooms + faculty for a timeslot chromosome.

        Multiple courses may share one room in the same timeslot if
        total students <= capacity. Large courses are split across
        rooms using free seats.
        """
        course_ts = {self.course_ids[i]: chrom[i] for i in range(self.n)}
        room_fill: Dict[int, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        fac_at: Dict[int, set] = defaultdict(set)
        fac_load: Dict[int, int] = defaultdict(int)
        assignment: Dict[int, List[Tuple[int, int, int]]] = {
            cid: [] for cid in self.course_ids
        }
        seat_map: Dict[Tuple[int, int, int], int] = {}

        order = sorted(self.course_ids, key=lambda c: self.enrolled[c], reverse=True)

        def free_in(ts: int, rid: int) -> int:
            return self.room_cap[rid] - room_fill[ts][rid]

        def try_pack(need: int, ts: int) -> Optional[List[Tuple[int, int]]]:
            """
            Balanced room selection:
            - Prefer rooms that already have some students (fill them first)
            - Among available rooms, prefer the one with the most free seats
              that is still a reasonable fit (avoid always picking the tiniest room)
            """
            candidates = []
            for rid in self.rooms_desc:
                free = free_in(ts, rid)
                if free >= need:
                    already_used = 1 if room_fill[ts][rid] > 0 else 0
                    # Score priority:
                    # 1. already used rooms first
                    # 2. then rooms with more free space (so we don't always pick Room 102)
                    score = (already_used, free)
                    candidates.append((score, rid))

            if candidates:
                # Sort: highest already_used first, then highest free seats
                candidates.sort(reverse=True)
                best_rid = candidates[0][1]
                return [(best_rid, need)]

            # Fallback – must split the course
            remaining = need
            plan: List[Tuple[int, int]] = []
            for rid in sorted(self.rooms_desc, key=lambda r: -free_in(ts, r)):
                if remaining <= 0:
                    break
                free = free_in(ts, rid)
                if free <= 0:
                    continue
                take = min(remaining, free)
                plan.append((rid, take))
                remaining -= take
            return plan if remaining <= 0 else None

        def pick_faculty(ts: int, n_needed: int) -> List[int]:
            free = [f for f in self.faculty_ids if f not in fac_at[ts]]
            free.sort(
                key=lambda f: (
                    fac_load[f],
                    -(self.faculty_max.get(f, 999) - fac_load[f]),
                )
            )
            chosen = free[:n_needed]
            if len(chosen) < n_needed:
                rest = sorted(
                    [f for f in self.faculty_ids if f not in chosen],
                    key=lambda f: fac_load[f],
                )
                chosen.extend(rest[: n_needed - len(chosen)])
            while len(chosen) < n_needed:
                chosen.append(self.rng.choice(self.faculty_ids))
            return chosen

        # Pass 1: place every course
        for cid in order:
            need = self.enrolled[cid]
            preferred = course_ts[cid]
            candidates = [preferred] + sorted(
                [t for t in self.timeslot_ids if t != preferred],
                key=lambda t: sum(room_fill[t].values()),
            )
            placed = False
            for ts in candidates:
                plan = try_pack(need, ts)
                if plan is None:
                    continue
                facs = pick_faculty(ts, len(plan))
                placements = []
                for (rid, seats), fac in zip(plan, facs):
                    placements.append((ts, rid, fac))
                    room_fill[ts][rid] += seats
                    fac_at[ts].add(fac)
                    fac_load[fac] += 1
                    seat_map[(cid, ts, rid)] = seats
                assignment[cid] = placements
                course_ts[cid] = ts
                placed = True
                break
            if not placed:
                rid = self.rooms_desc[0]
                fac = min(self.faculty_ids, key=lambda f: fac_load[f])
                ts = preferred
                assignment[cid] = [(ts, rid, fac)]
                room_fill[ts][rid] += need
                fac_at[ts].add(fac)
                fac_load[fac] += 1
                seat_map[(cid, ts, rid)] = need

        # Pass 2: resolve student clashes
        def has_clash(cid: int) -> bool:
            ts = course_ts[cid]
            for other, cnt in self.neighbours[cid]:
                if cnt and course_ts.get(other) == ts:
                    return True
            return False

        for cid in order:
            if not has_clash(cid):
                continue
            need = self.enrolled[cid]
            cur = course_ts[cid]
            for new_ts in sorted(
                [t for t in self.timeslot_ids if t != cur],
                key=lambda t: sum(room_fill[t].values()),
            ):
                bad = False
                for other, cnt in self.neighbours[cid]:
                    if cnt and course_ts.get(other) == new_ts:
                        bad = True
                        break
                if bad:
                    continue
                plan = try_pack(need, new_ts)
                if plan is None:
                    continue
                for ots, orid, ofac in assignment[cid]:
                    seats = seat_map.pop((cid, ots, orid), need)
                    room_fill[ots][orid] = max(0, room_fill[ots][orid] - seats)
                    fac_at[ots].discard(ofac)
                    fac_load[ofac] = max(0, fac_load[ofac] - 1)
                facs = pick_faculty(new_ts, len(plan))
                new_pl = []
                for (rid, seats), fac in zip(plan, facs):
                    new_pl.append((new_ts, rid, fac))
                    room_fill[new_ts][rid] += seats
                    fac_at[new_ts].add(fac)
                    fac_load[fac] += 1
                    seat_map[(cid, new_ts, rid)] = seats
                assignment[cid] = new_pl
                course_ts[cid] = new_ts
                break

        # Pass 3: rebalance overloaded faculty
        for _ in range(4):
            overloaded = [
                f for f, load in fac_load.items()
                if load > self.faculty_max.get(f, 999)
            ]
            if not overloaded:
                break
            for ofac in overloaded:
                for cid, places in list(assignment.items()):
                    new_places = []
                    changed = False
                    for ts, rid, fac in places:
                        if fac != ofac:
                            new_places.append((ts, rid, fac))
                            continue
                        alts = [
                            f for f in self.faculty_ids
                            if f not in fac_at[ts]
                            and fac_load[f] < self.faculty_max.get(f, 999)
                        ]
                        if not alts:
                            alts = [
                                f for f in self.faculty_ids
                                if fac_load[f] < fac_load[ofac]
                            ]
                        if alts:
                            nf = min(alts, key=lambda f: fac_load[f])
                            fac_at[ts].discard(ofac)
                            fac_at[ts].add(nf)
                            fac_load[ofac] = max(0, fac_load[ofac] - 1)
                            fac_load[nf] += 1
                            new_places.append((ts, rid, nf))
                            changed = True
                        else:
                            new_places.append((ts, rid, fac))
                    if changed:
                        assignment[cid] = new_places

        # Count hard conflicts
        hard = 0
        details: Dict[str, list] = {
            "student_clashes": [],
            "room_overflows": [],
            "faculty_double": [],
            "faculty_overload": [],
        }

        for (a, b), cnt in self.shared.items():
            if course_ts.get(a) == course_ts.get(b) and cnt > 0:
                hard += cnt
                details["student_clashes"].append({
                    "courses": (self.code[a], self.code[b]),
                    "shared": cnt,
                    "timeslot": course_ts[a],
                })

        for ts, rooms in room_fill.items():
            for rid, filled in rooms.items():
                cap = self.room_cap[rid]
                if filled > cap:
                    over = filled - cap
                    hard += over
                    details["room_overflows"].append({
                        "room": self.room_name[rid],
                        "timeslot": ts,
                        "filled": filled,
                        "capacity": cap,
                        "overflow": over,
                    })

        fac_cnt = Counter()
        for places in assignment.values():
            for ts, _, fac in places:
                fac_cnt[(ts, fac)] += 1
        for (ts, fac), cnt in fac_cnt.items():
            if cnt > 1:
                hard += cnt - 1
                details["faculty_double"].append({
                    "faculty": self.fac_name[fac],
                    "timeslot": ts,
                    "count": cnt,
                })

        for fac, load in fac_load.items():
            mx = self.faculty_max.get(fac, 999)
            if load > mx:
                hard += load - mx
                details["faculty_overload"].append({
                    "faculty": self.fac_name[fac],
                    "load": load,
                    "max": mx,
                    "over": load - mx,
                })

        return assignment, hard, details

    def _soft_cost(self, assignment: Dict[int, List[Tuple[int, int, int]]]) -> int:
        course_ts = {}
        for cid, places in assignment.items():
            if places:
                course_ts[cid] = places[0][0]
        cost = 0
        for (a, b), cnt in self.shared.items():
            ta, tb = course_ts.get(a), course_ts.get(b)
            if ta is None or tb is None or ta == tb:
                continue
            if self.ts_day.get(ta) == self.ts_day.get(tb):
                cost += P_SAME_DAY * cnt
                if abs(self.ts_order.get(ta, 0) - self.ts_order.get(tb, 0)) == 1:
                    cost += P_BACK_TO_BACK * cnt
        return cost

    def evaluate(self, chrom: List[int]) -> Tuple[float, int, Dict, dict]:
        assignment, hard, details = self._pack(chrom)
        soft = self._soft_cost(assignment)
        total = hard * 1000 + soft
        return total, hard, assignment, details


class ExamSchedulerGA(ExamSchedulerBase):
    def __init__(self, *args, mutation_rate: float = 0.20, elite_count: int = 10, **kw):
        super().__init__(*args, **kw)
        self.mutation_rate = mutation_rate
        self.elite_count = min(elite_count, self.population_size // 2)

    def _crossover(self, a: List[int], b: List[int]) -> List[int]:
        return [x if self.rng.random() < 0.5 else y for x, y in zip(a, b)]

    def _mutate(self, chrom: List[int], rate: float) -> List[int]:
        out = list(chrom)
        for i in range(self.n):
            if self.rng.random() < rate:
                out[i] = self.rng.choice(self.timeslot_ids)
        return out

    def _tournament(self, scored: list, k: int = 3) -> List[int]:
        sample = self.rng.sample(scored, min(k, len(scored)))
        return min(sample, key=lambda x: (x[1], x[0]))[2]

    def run(self, progress_callback=None) -> dict:
        t0 = time.time()
        pop: List[List[int]] = [self._smart_chrom() for _ in range(self.elite_count)]
        while len(pop) < self.population_size:
            pop.append(self._random_chrom())

        best_assign = None
        best_details: dict = {}
        best_hard = float("inf")
        best_total = float("inf")
        history = []

        for gen in range(1, self.generations + 1):
            scored = []
            for chrom in pop:
                total, hard, assign, details = self.evaluate(chrom)
                scored.append((total, hard, chrom, assign, details))
            scored.sort(key=lambda x: (x[1], x[0]))

            t, h, _, a, d = scored[0]
            if h < best_hard or (h == best_hard and t < best_total):
                best_hard, best_total = h, t
                best_assign, best_details = a, d

            history.append({
                "generation": gen,
                "best_cost": best_total,
                "hard_conflicts": best_hard,
            })
            if progress_callback and (gen == 1 or gen % 10 == 0):
                progress_callback(gen, best_total, best_hard)
            if best_hard == 0 and gen >= 5:
                break

            next_pop = [s[2] for s in scored[: self.elite_count]]
            rate = self.mutation_rate
            if best_hard > 0 and gen > 25:
                rate = min(0.45, self.mutation_rate + 0.15)
            while len(next_pop) < self.population_size:
                p1 = self._tournament(scored)
                p2 = self._tournament(scored)
                child = self._mutate(self._crossover(p1, p2), rate)
                next_pop.append(child)
            pop = next_pop

        return {
            "run_id": str(uuid.uuid4())[:8],
            "assignment": best_assign or {},
            "final_cost": best_total if best_total != float("inf") else 0,
            "hard_conflicts": int(best_hard) if best_hard != float("inf") else 0,
            "conflict_details": best_details,
            "generations_run": history[-1]["generation"] if history else 0,
            "seconds_taken": round(time.time() - t0, 2),
            "history": history,
        }


class ExamSchedulerPSO(ExamSchedulerBase):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.w, self.c1, self.c2 = 0.25, 0.40, 0.35

    def run(self, progress_callback=None) -> dict:
        t0 = time.time()
        particles = [
            self._smart_chrom() if i < 8 else self._random_chrom()
            for i in range(self.population_size)
        ]
        pbest = [list(p) for p in particles]
        pbest_score = [self.evaluate(p)[:2] for p in particles]
        gi = min(range(len(pbest_score)), key=lambda i: (pbest_score[i][1], pbest_score[i][0]))
        gbest = list(particles[gi])
        g_total, g_hard = pbest_score[gi]
        _, _, best_assign, best_details = self.evaluate(gbest)
        history = []
        for gen in range(1, self.generations + 1):
            for i in range(self.population_size):
                new = []
                for j in range(self.n):
                    r = self.rng.random()
                    if r < self.w:
                        new.append(particles[i][j])
                    elif r < self.w + self.c1:
                        new.append(pbest[i][j])
                    elif r < self.w + self.c1 + self.c2:
                        new.append(gbest[j])
                    else:
                        new.append(self.rng.choice(self.timeslot_ids))
                particles[i] = new
                total, hard, assign, details = self.evaluate(new)
                if hard < pbest_score[i][1] or (
                    hard == pbest_score[i][1] and total < pbest_score[i][0]
                ):
                    pbest[i] = new
                    pbest_score[i] = (total, hard)
                    if hard < g_hard or (hard == g_hard and total < g_total):
                        gbest, g_total, g_hard = new, total, hard
                        best_assign, best_details = assign, details
            history.append({
                "generation": gen,
                "best_cost": g_total,
                "hard_conflicts": g_hard,
            })
            if progress_callback and (gen == 1 or gen % 10 == 0):
                progress_callback(gen, g_total, g_hard)
            if g_hard == 0 and gen >= 5:
                break
        return {
            "run_id": str(uuid.uuid4())[:8],
            "assignment": best_assign or {},
            "final_cost": g_total,
            "hard_conflicts": int(g_hard),
            "conflict_details": best_details,
            "generations_run": history[-1]["generation"] if history else 0,
            "seconds_taken": round(time.time() - t0, 2),
            "history": history,
        }


class ExamSchedulerDE(ExamSchedulerBase):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.CR, self.F = 0.85, 0.5

    def run(self, progress_callback=None) -> dict:
        t0 = time.time()
        pop = [
            self._smart_chrom() if i < 8 else self._random_chrom()
            for i in range(self.population_size)
        ]
        scores = [self.evaluate(p)[:2] for p in pop]
        bi = min(range(len(scores)), key=lambda i: (scores[i][1], scores[i][0]))
        best = list(pop[bi])
        b_total, b_hard = scores[bi]
        _, _, best_assign, best_details = self.evaluate(best)
        history = []
        for gen in range(1, self.generations + 1):
            for i in range(self.population_size):
                idxs = [x for x in range(self.population_size) if x != i]
                r1, r2, r3 = self.rng.sample(idxs, 3)
                trial = []
                for j in range(self.n):
                    if self.rng.random() < self.CR:
                        trial.append(
                            self.rng.choice(self.timeslot_ids)
                            if self.rng.random() < self.F
                            else pop[r1][j]
                        )
                    else:
                        trial.append(pop[i][j])
                total, hard, assign, details = self.evaluate(trial)
                if hard < scores[i][1] or (
                    hard == scores[i][1] and total < scores[i][0]
                ):
                    pop[i] = trial
                    scores[i] = (total, hard)
                    if hard < b_hard or (hard == b_hard and total < b_total):
                        best, b_total, b_hard = trial, total, hard
                        best_assign, best_details = assign, details
            history.append({
                "generation": gen,
                "best_cost": b_total,
                "hard_conflicts": b_hard,
            })
            if progress_callback and (gen == 1 or gen % 10 == 0):
                progress_callback(gen, b_total, b_hard)
            if b_hard == 0 and gen >= 5:
                break
        return {
            "run_id": str(uuid.uuid4())[:8],
            "assignment": best_assign or {},
            "final_cost": b_total,
            "hard_conflicts": int(b_hard),
            "conflict_details": best_details,
            "generations_run": history[-1]["generation"] if history else 0,
            "seconds_taken": round(time.time() - t0, 2),
            "history": history,
        }


def diagnose_feasibility(courses, students_by_course, rooms, faculty, timeslots):
    problems, suggestions = [], []
    n_c, n_r, n_s, n_f = len(courses), len(rooms), len(timeslots), len(faculty)
    max_cap = max((r["capacity"] for r in rooms), default=0)
    fac_cap = sum(f.get("max_invigilations", 10) for f in faculty)
    enrolled = {
        c["id"]: len(students_by_course.get(c["id"], ())) for c in courses
    }
    large = sum(1 for n in enrolled.values() if n > max_cap)

    if n_s < 8 and n_c > 20:
        problems.append(
            f"Only {n_s} timeslots for {n_c} courses — shared-student pairs may clash."
        )
        suggestions.append("Add more timeslots (recommended 10–12).")
    if n_s < 6:
        problems.append("Fewer than 6 timeslots makes zero clashes very hard.")
        suggestions.append("Increase the number of timeslots.")
    if n_r < 4:
        suggestions.append("Adding 1–2 more large rooms helps packing.")
    if fac_cap < n_c:
        problems.append(
            f"Faculty capacity low ({fac_cap} max invigilations for {n_c} exams)."
        )
        suggestions.append("Increase max_invigilations or add faculty.")
    if large and max_cap:
        suggestions.append(
            f"{large} course(s) exceed largest room ({max_cap}) and will be split."
        )
    return {
        "feasible": len(problems) == 0,
        "problems": problems,
        "suggestions": suggestions,
        "stats": {
            "courses": n_c,
            "rooms": n_r,
            "timeslots": n_s,
            "room_slots": n_r * n_s,
            "faculty": n_f,
            "max_room_capacity": max_cap,
            "total_faculty_capacity": fac_cap,
        },
    }


def diagnose_schedule(conflict_details, hard_conflicts, **_kwargs):
    if hard_conflicts == 0:
        return {
            "feasible": True,
            "problems": [],
            "suggestions": ["Schedule is clash-free. You can publish the timetable."],
            "stats": {},
        }
    details = conflict_details or {}
    problems, suggestions = [], []

    for item in details.get("student_clashes", []):
        a, b = item["courses"]
        problems.append(
            f"Student clash: {a} and {b} share {item['shared']} student(s) "
            f"in the same timeslot."
        )
    if details.get("student_clashes"):
        suggestions.append(
            "Move one clashing course to another timeslot, or add more timeslots."
        )
    for item in details.get("room_overflows", []):
        problems.append(
            f"Room overflow: {item['room']} has {item['filled']} students "
            f"(capacity {item['capacity']}, overflow {item['overflow']})."
        )
    if details.get("room_overflows"):
        suggestions.append("Add larger / more rooms, or spread courses across slots.")
    for item in details.get("faculty_double", []):
        problems.append(
            f"Faculty double-booked: {item['faculty']} has {item['count']} "
            f"duties in the same timeslot."
        )
    if details.get("faculty_double"):
        suggestions.append("Add faculty or reduce multi-room exams per slot.")
    for item in details.get("faculty_overload", []):
        problems.append(
            f"Faculty overload: {item['faculty']} has {item['load']} invigilations "
            f"(max {item['max']})."
        )
    if details.get("faculty_overload"):
        suggestions.append(
            "Increase max_invigilations for overloaded faculty, or add faculty."
        )
    if not problems:
        problems.append(f"{hard_conflicts} hard conflict(s) remain.")
        suggestions.append(
            "Re-run with larger population / more generations, or add resources."
        )
    return {
        "feasible": False,
        "problems": problems,
        "suggestions": suggestions,
        "stats": {},
    }