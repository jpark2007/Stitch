"""Generator for the hand-authored hymn-book reference sample in web/public/samples/.

Run: cd api && uv run python tests/gen_hymn_books_sample.py
The scene is authored by hand below; this script only does the arithmetic (FK, two-bone IK,
walk timing) so the reference timeline is physically consistent. The compiler is tested
against its output."""
import json, math, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RIG = json.loads((REPO / "shared/rig/puppet.json").read_text())
POSES = json.loads((REPO / "shared/rig/poses.json").read_text())
VOC = json.loads((REPO / "shared/vocabulary.json").read_text())
OUT = REPO / "web/public/samples"

Z = 0.5            # acting plane
RISE = 1.2         # set rise before shot t=0
R = lambda v: round(v, 3)
def rv(v): return [R(x) for x in v]

def scale(build): return RIG["stageHeight"] * VOC["builds"][build]

def rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)

def bone_frame(bone, rots):
    """Local (rig-unit) position of a bone's pivot and its accumulated angle; origin = hip."""
    if bone == "root":
        return (0.0, 0.0), 0.0
    b = RIG["bones"][bone]
    ppos, pang = bone_frame(b["parent"], rots)
    off = rot(b["pivot"], pang)
    return (ppos[0] + off[0], ppos[1] + off[1]), pang + rots.get(bone, 0.0)

def socket_world(socket, rots, ground, facing, build):
    s = scale(build)
    sk = RIG["sockets"][socket]
    pos, ang = bone_frame(sk["bone"], rots)
    off = rot(sk["at"], ang)
    lx, ly = pos[0] + off[0], pos[1] + off[1] + RIG["hipHeight"]
    return (ground[0] + facing * lx * s, ground[1] + ly * s)

def shoulder_world(arm, ground, facing, build):
    s = scale(build)
    p = RIG["bones"][RIG["ik"][arm]["upper"]]["pivot"]
    return (ground[0] + facing * p[0] * s, ground[1] + (p[1] + RIG["hipHeight"]) * s)

def ik(arm, target, ground, facing, build):
    """Two-bone IK. Returns (upper_rot, lower_rot) putting the hand socket on target (world x, y)."""
    s = scale(build)
    sh = shoulder_world(arm, ground, facing, build)
    vx, vy = facing * (target[0] - sh[0]) / s, (target[1] - sh[1]) / s
    L = RIG["bones"][RIG["ik"][arm]["upper"]]["length"]
    d = math.hypot(vx, vy)
    assert d <= 2 * L + 1e-9, f"out of reach: {d:.3f} > {2*L:.3f}"
    theta = math.atan2(vx, -vy)
    a = math.acos(min(1.0, d / (2 * L)))
    up, lo = theta - a, 2 * a
    got = socket_world(RIG["ik"][arm]["socket"], {RIG["ik"][arm]["upper"]: up, RIG["ik"][arm]["lower"]: lo}, ground, facing, build)
    assert abs(got[0] - target[0]) < 1e-6 and abs(got[1] - target[1]) < 1e-6, (got, target)
    return up, lo

def mark_pos(m):
    return (2 * m[0] - 1, m[1])

# ---------------------------------------------------------------- scene definition
MARKS = {"steps_left": [0.2, 0.0], "steps_foot": [0.5, 0.0], "steps_top": [0.6, 0.3], "curb": [0.9, 0.0]}
ALL_MARKS = {**MARKS, **VOC["builtinMarks"]}
CAST = {"dad": "tall", "mom": "average"}
WALK = VOC["speeds"]["walk"]
BOOK_H = VOC["sizes"]["hand"]
HOLD = POSES["poses"]["hold"]

screenplay = {
    "memoryId": "sample_hymn_books",
    "cast": ["dad", "mom"],
    "props": ["hymn_books"],
    "shots": [{
        "id": "s1",
        "set": "church_steps_1988",
        "marks": MARKS,
        "start": {"dad": {"at": "offstage_left", "facing": "right"},
                  "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}},
        "camera": [{"t": 0, "move": "wide"}, {"t": 5.5, "move": "dolly_to", "target": "mom", "dur": 3}],
        "actions": [
            {"t": 0,    "who": "dad", "do": "enter", "from": "offstage_left", "to": "steps_left"},
            {"t": 3.25, "who": "mom", "do": "drop", "what": "hymn_books", "id": "drop"},
            {"t": 3.6,  "who": "dad", "do": "walk_to", "to": "steps_foot"},
            {"t": 5.5,  "who": "dad", "do": "pick_up", "what": "hymn_books"},
            {"t": 6.6,  "who": "dad", "do": "hand_to", "what": "hymn_books", "to": "mom", "id": "hand_back"},
            {"t": 7.7,  "who": "mom", "do": "pose", "pose": "bow_head"},
        ],
        "narration": [
            {"t": 0,   "text": "I saw her at the top of the church steps…", "source": "telling:dad:s2"},
            {"t": 2.5, "text": "…and then she dropped every single hymn book.", "source": "telling:dad:s3", "cue": "drop", "cueWord": "dropped"},
            {"t": 6.35, "text": "I handed them back, and she wouldn't even look at me.", "source": "telling:dad:s4", "cue": "hand_back", "cueWord": "handed"},
        ],
        "transition": "fold",
        "pageText": "One Sunday in 1988, a stack of hymn books changed everything.",
    }],
    "narrations": {"mom": {"s1": [
        {"t": 0,   "text": "I was carrying far too many hymn books.", "source": "telling:mom:s1"},
        {"t": 2.0, "text": "And of course they went everywhere, right in front of him.", "source": "telling:mom:s2", "cue": "drop", "cueWord": "everywhere"},
        {"t": 6.35, "text": "He handed them back so carefully. I couldn't look up.", "source": "telling:mom:s3", "cue": "hand_back", "cueWord": "handed"},
    ]}},
    "interactions": [{
        "id": "drop_books", "kind": "pull_tab", "label": "Pull to drop the books", "shot": "s1",
        "page": "stage", "at": [0.85, 0.82],
        "drives": {"type": "scrub", "from": 3.25, "to": 3.55, "targets": ["prop:hymn_books", "puppet:mom"]},
    }],
    "explanations": [
        {"target": "puppet:dad", "text": "Dad was 26 and had only come to church that Sunday because his mother asked."},
        {"target": "puppet:mom", "text": "Mom sang in the choir, and carried the hymn books in every week."},
        {"target": "prop:hymn_books", "text": "The hymn books Mom dropped on the steps. Dad says it is the best thing that ever fell at his feet."},
    ],
}

# ------------------------------------------------------------------------- compile by hand
tracks = {}
def key(target, channel, t, v):
    tracks.setdefault((target, channel), []).append([R(t), v if isinstance(v, str) else (rv(v) if isinstance(v, (list, tuple)) else R(v))])

A = lambda t: RISE + t   # shot time -> timeline time
LIMBS = ["leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower", "arm_l.upper", "arm_l.lower", "arm_r.upper", "arm_r.lower"]

def walk(who, t0, x0, x1, y, skip=()):
    """Root slide plus walk-cycle keys. Returns arrival time (shot time)."""
    dist = abs(x1 - x0)
    t1 = t0 + dist / WALK
    key(f"puppet:{who}", "root.position", A(t0), (x0, y, Z))
    key(f"puppet:{who}", "root.position", A(t1), (x1, y, Z))
    cyc = POSES["cycles"]["walk"]
    stride = cyc["strideLength"] * scale(CAST[who])
    cycles = dist / stride
    n = max(1, round(cycles * 4))           # quarter-cycle keys
    bones = [b for b in LIMBS if b not in skip]
    for b in bones:
        key(f"puppet:{who}", b + ".rot", A(t0), 0.0)
    for i in range(1, n):
        f = cyc["frames"][i % 4]
        for b in bones:
            key(f"puppet:{who}", b + ".rot", A(t0 + (t1 - t0) * i / n), f.get(b, 0.0))
    for b in bones:
        key(f"puppet:{who}", b + ".rot", A(t1), 0.0)
    return t1

def arm(who, t, up, lo, side="arm_r"):
    key(f"puppet:{who}", RIG["ik"][side]["upper"] + ".rot", A(t), up)
    key(f"puppet:{who}", RIG["ik"][side]["lower"] + ".rot", A(t), lo)

P = {k: mark_pos(v) for k, v in ALL_MARKS.items()}
dad0, dad1, dad2, mom_at = P["offstage_left"], P["steps_left"], P["steps_foot"], P["steps_top"]
hold_up, hold_lo = HOLD["arm_r.upper"], HOLD["arm_r.lower"]

# initial state at timeline t=0
key("puppet:dad", "root.position", 0, (dad0[0], dad0[1], Z))
key("puppet:dad", "root.facing", 0, 1)
key("puppet:mom", "root.position", 0, (mom_at[0], mom_at[1], Z))
key("puppet:mom", "root.facing", 0, -1)
key("puppet:mom", "head.rot", 0, 0.0)
arm("mom", -RISE, hold_up, hold_lo)
key("prop:hymn_books", "parent", 0, "puppet:mom.hand_r")
key("prop:hymn_books", "position", 0, (0, 0, 0))

# t=0 dad enters to steps_left
arrive1 = walk("dad", 0, dad0[0], dad1[0], 0.0)

# t=3.25 mom drops the books: they fall from her hand to the step beside her feet
T_DROP, FALL = 3.25, 0.3
hand = socket_world("hand_r", {"arm_r.upper": hold_up, "arm_r.lower": hold_lo}, mom_at, -1, CAST["mom"])
land = (mom_at[0] - 0.08, mom_at[1])
arm("mom", T_DROP, hold_up, hold_lo)
arm("mom", T_DROP + FALL, 0.0, 0.0)
key("prop:hymn_books", "parent", A(T_DROP), "ground")
key("prop:hymn_books", "position", A(T_DROP), (hand[0], hand[1] - BOOK_H, Z))   # anchor = grip - height
key("prop:hymn_books", "position", A(T_DROP + FALL), (land[0], land[1], Z))

# t=3.6 dad walks to the foot of the steps
arrive2 = walk("dad", 3.6, dad1[0], dad2[0], 0.0)

# t=5.5 dad picks the books up (reach 0.4s, grab, raise into hold 0.4s)
T_PICK, REACH = 5.5, 0.4
assert arrive2 <= T_PICK, arrive2
grip = (land[0], land[1] + BOOK_H)
up, lo = ik("arm_r", grip, dad2, 1, CAST["dad"])
arm("dad", T_PICK, 0.0, 0.0)
arm("dad", T_PICK + REACH, up, lo)
arm("dad", T_PICK + 2 * REACH, hold_up, hold_lo)
key("prop:hymn_books", "parent", A(T_PICK + REACH), "puppet:dad.hand_r")
key("prop:hymn_books", "position", A(T_PICK + REACH), (0, 0, 0))

# t=6.6 dad hands them to mom: both hands meet at a point between the shoulders
T_HAND, PASS = 6.6, 0.5
sd = shoulder_world("arm_r", dad2, 1, CAST["dad"])
sm = shoulder_world("arm_r", mom_at, -1, CAST["mom"])
reach_d = 2 * RIG["bones"]["arm_r.upper"]["length"] * scale(CAST["dad"])
gap = math.hypot(sm[0] - sd[0], sm[1] - sd[1])
f = (reach_d * 0.83) / gap
meet = (sd[0] + (sm[0] - sd[0]) * f, sd[1] + (sm[1] - sd[1]) * f)
dup, dlo = ik("arm_r", meet, dad2, 1, CAST["dad"])
mup, mlo = ik("arm_r", meet, mom_at, -1, CAST["mom"])
arm("dad", T_HAND, hold_up, hold_lo); arm("dad", T_HAND + PASS, dup, dlo); arm("dad", T_HAND + PASS + 0.4, 0.0, 0.0)
arm("mom", T_HAND, 0.0, 0.0);        arm("mom", T_HAND + PASS, mup, mlo); arm("mom", T_HAND + PASS + 0.4, hold_up, hold_lo)
key("prop:hymn_books", "parent", A(T_HAND + PASS), "puppet:mom.hand_r")
key("prop:hymn_books", "position", A(T_HAND + PASS), (0, 0, 0))

# t=7.7 mom bows her head
bow = POSES["poses"]["bow_head"]["head"]
key("puppet:mom", "head.rot", A(7.7), 0.0)
key("puppet:mom", "head.rot", A(8.1), bow)

# set rises (back first), camera flies in, then dollies to mom
for i, start in enumerate([0.0, 0.2, 0.4]):
    key(f"set:church_steps_1988.layer{i}", "fold", start, 0.0)
    key(f"set:church_steps_1988.layer{i}", "fold", start + 0.8, 1.0)
WIDE_P, WIDE_L = (0, 0.6, 2.6), (0, 0.35, Z)
key("camera", "position", 0, (0, 1.1, 3.4)); key("camera", "lookAt", 0, (0, 0.2, Z)); key("camera", "fov", 0, 35)
key("camera", "position", RISE, WIDE_P);     key("camera", "lookAt", RISE, WIDE_L)
key("camera", "position", A(5.5), WIDE_P);   key("camera", "lookAt", A(5.5), WIDE_L)
key("camera", "position", A(8.5), (mom_at[0], 0.6, 1.5)); key("camera", "lookAt", A(8.5), (mom_at[0], mom_at[1] + 0.25, Z))

# narration: 0.25 s per word; cued actions already sit on their words
def words(line):
    toks = [w.strip("….,") for w in line["text"].split()]
    return [{"t": R(A(line["t"]) + 0.25 * i), "word": w} for i, w in enumerate(toks)]

def narr(lines):
    out = []
    for l in lines:
        out.append({**l, "t": R(A(l["t"])), "words": words(l)})
    return out

shot = screenplay["shots"][0]
dad_lines, mom_lines = narr(shot["narration"]), narr(screenplay["narrations"]["mom"]["s1"])
for lines, action_t in ((dad_lines, None), (mom_lines, None)):
    for l in lines:
        if l.get("cue"):
            act = next(a for a in shot["actions"] if a.get("id") == l["cue"])
            w = next(w for w in l["words"] if w["word"] == l["cueWord"])
            assert abs(w["t"] - A(act["t"])) < 1e-6, (l["text"], w["t"], A(act["t"]))
end_shot = max(w["t"] for l in dad_lines + mom_lines for w in l["words"]) + 0.6
DURATION = R(end_shot)

caps = lambda lines: [{"t": l["t"], "text": l["text"]} for l in lines]
ordered = []
for (target, channel), keys in tracks.items():
    keys.sort(key=lambda k: k[0])
    dedup = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
    ordered.append({"target": target, "channel": channel, "keys": dedup})
ordered.sort(key=lambda t: (t["target"], t["channel"]))

timeline = {
    "duration": DURATION,
    "audio": None,
    "tracks": ordered,
    "captions": caps(dad_lines),
    "shots": [{"id": "s1", "set": "church_steps_1988", "start": 0, "actionStart": RISE, "end": DURATION,
               "transition": "fold", "pageText": shot["pageText"]}],
    "effects": [],
    "narrations": {
        "dad": {"authorId": "dad", "audio": None, "captions": caps(dad_lines), "lines": dad_lines},
        "mom": {"authorId": "mom", "audio": None, "captions": caps(mom_lines), "lines": mom_lines},
    },
    "defaultNarrator": "dad",
    "interactions": [{**screenplay["interactions"][0],
                      "drives": {**screenplay["interactions"][0]["drives"], "from": R(A(3.25)), "to": R(A(3.55))}}],
    "explanations": screenplay["explanations"],
}

people = [
    {"id": "dad", "familyId": "sample_family", "userId": None, "name": "David", "born": 1962,
     "parentIds": [], "spouseIds": ["mom"], "character": {"color": "#3f6fb5", "item": "blue flat cap", "build": "tall"}},
    {"id": "mom", "familyId": "sample_family", "userId": None, "name": "Ana", "born": 1964,
     "parentIds": [], "spouseIds": ["dad"], "character": {"color": "#c0504d", "item": "red scarf", "build": "average"}},
]
props = [{"id": "hymn_books", "familyId": "sample_family", "name": "hymn books", "description": "a stack of worn blue hymn books",
          "source": "generated", "size": "hand", "art": {"body": None}, "color": "#2f4a7a", "aspect": 1.0,
          "anchor": [0.5, 1.0], "grip": [0.5, 0.0], "sockets": {}, "parts": {}, "states": {}}]
sets = [{"id": "church_steps_1988", "familyId": "sample_family", "name": "St. Mary's church steps",
         "description": "brick church front with wide stone steps",
         "layers": [{"art": None, "depth": 0, "height": 1.0, "color": "#b5654a"},
                    {"art": None, "depth": 1, "height": 0.45, "color": "#cfc6b4"},
                    {"art": None, "depth": 2, "height": 0.18, "color": "#7d9a5b"}],
         "marks": MARKS}]
memory = {
    "id": "sample_hymn_books", "familyId": "sample_family", "title": "How I met your mother", "year": 1988, "era": "1980s",
    "participantIds": ["dad", "mom"],
    "tellings": [
        {"authorId": "dad", "question": "How did you meet your wife?", "photoIds": [],
         "text": "It was a Sunday in 1988. I saw her at the top of the church steps. And then she dropped every single hymn book. I handed them back, and she wouldn't even look at me."},
        {"authorId": "mom", "question": "Dad says you met on the church steps. What do you remember?", "photoIds": [],
         "text": "I was carrying far too many hymn books. Of course they went everywhere, right in front of him. He handed them back so carefully. I couldn't look up."},
    ],
    "screenplay": screenplay, "timeline": timeline, "propIds": ["hymn_books"], "setIds": ["church_steps_1988"], "version": 1,
}

OUT.mkdir(parents=True, exist_ok=True)
dump = lambda name, obj: (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
dump("hymn_books.screenplay.json", screenplay)
dump("hymn_books.timeline.json", timeline)
dump("hymn_books.memory.json", {"memory": memory, "props": props, "sets": sets, "people": people})

print("duration", DURATION, "tracks", len(ordered))
print("dad arrives steps_left", R(arrive1), "steps_foot", R(arrive2))
print("mom hand (hold)", rv(hand), "books land", rv(land))
print("pick-up IK", R(up), R(lo), "| handover point", rv(meet), "dad", R(dup), R(dlo), "mom", R(mup), R(mlo))
