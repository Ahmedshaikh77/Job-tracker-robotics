"""Sanity test for ROBOTICS filter logic."""
import yaml
from src.filters import JobFilter
from src.models import Job

cfg = yaml.safe_load(open("config.yaml"))
f = JobFilter(cfg)

test_cases = [
    # SHOULD PASS:
    (Job("Medtronic", "1", "R&D Engineer, Surgical Robotics", "Minneapolis, MN, USA", "url"), True, "R&D + surgical robotics"),
    (Job("Boston Dynamics", "2", "Robotics Software Engineer, New Grad", "Waltham, MA, USA", "url"), True, "robotics SW + new grad"),
    (Job("Stryker", "3", "Mechanical Design Engineer", "Kalamazoo, MI, USA", "url"), True, "mech design + MI"),
    (Job("NVIDIA", "4", "Embedded Systems Engineer", "Santa Clara, CA, USA", "url"), True, "embedded + CA"),
    (Job("Skydio", "5", "Controls Engineer (Entry Level)", "San Mateo, CA, USA", "url"), True, "controls + entry"),
    (Job("Waymo", "6", "Motion Planning Engineer", "Mountain View, CA, USA", "url"), True, "motion planning"),
    (Job("Apptronik", "7", "Mechatronics Engineer", "Austin, TX, USA", "url"), True, "mechatronics + TX"),
    (Job("Formlabs", "8", "Test Engineer, Hardware", "Somerville, MA, US", "url"), True, "test engineer + MA"),
    # SHOULD FAIL - seniority:
    (Job("Medtronic", "10", "Senior Robotics Engineer", "Minneapolis, MN, USA", "url"), False, "senior"),
    (Job("Tesla", "11", "Staff Mechanical Engineer", "Palo Alto, CA, USA", "url"), False, "staff"),
    (Job("NVIDIA", "12", "Principal Embedded Engineer", "Santa Clara, CA, USA", "url"), False, "principal"),
    (Job("Stryker", "13", "Engineering Manager, Robotics", "Mahwah, NJ, USA", "url"), False, "manager"),
    # SHOULD FAIL - clearance/citizenship (international student):
    (Job("Anduril", "20", "Robotics Engineer", "Costa Mesa, CA, USA", "url", "Must be a U.S. Citizen. Active security clearance required."), False, "clearance"),
    (Job("Shield AI", "21", "Embedded Systems Engineer", "San Diego, CA, USA", "url", "This role requires ITAR compliance and US citizenship is required."), False, "ITAR"),
    # SHOULD FAIL - wrong location:
    (Job("Medtronic", "30", "Robotics Engineer", "Galway, Ireland", "url"), False, "non-US"),
    # SHOULD FAIL - wrong family:
    (Job("Stryker", "40", "Sales Representative, Surgical", "Chicago, IL, USA", "url"), False, "sales"),
    (Job("NVIDIA", "41", "Data Center Recruiter", "Santa Clara, CA, USA", "url"), False, "recruiter"),
]

print(f"{'RESULT':<10} {'EXP':<6} {'SCORE':<6} {'TITLE':<45} {'LOCATION':<28}")
print("-" * 115)
correct = 0
for job, expected, reason in test_cases:
    passed, score, bd = f.passes(job)
    match = "OK" if passed == expected else "XX WRONG"
    exp = "PASS" if expected else "FAIL"
    if passed == expected:
        correct += 1
    note = f"[clearance:{bd['hard_exclude_hits']}]" if bd.get("hard_exclude_hits") else ""
    print(f"{match:<10} {exp:<6} {score:<6} {job.title[:45]:<45} {job.location[:28]:<28} {note} ({reason})")
print("-" * 115)
print(f"Correct: {correct}/{len(test_cases)}")
