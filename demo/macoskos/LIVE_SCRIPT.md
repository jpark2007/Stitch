# Live demo script: the Macoskos

The names and stories are invented for the demo. The family photos are not in this repo.

Start the demo with the run command at the bottom. Every account's password is `stitch-demo`.

| Who | Email | Role |
|---|---|---|
| Jenna Macosko (mom, the presenter) | jenna@macosko.demo | admin |
| Rick Macosko (grandpa, on the phone) | rick@macosko.demo | member |
| Daniel, Linda, Carol, Amy, Harold, Evelyn | daniel@, linda@, carol@, amy@, harold@, evelyn@macosko.demo | member |

The pre-made book has 13 moments. It does **not** have "Marmalade comes home" (#7) or "Matching overalls on the lake" (#5): those two are told live and replayed onto their prepared scenes.

## 1. Jenna types "Marmalade comes home" on /add

Signed in as jenna@macosko.demo, open **Add a memory**.

**Your story** (type or paste exactly; a typo or a dropped word still matches, and any telling with the word "Marmalade" in it matches too):

> Mom swore she'd never get another cat, then came home from the shelter with an orange kitten in her coat. We named him Marmalade before we'd even closed the door.

**When was this?** `1999`

Photo (optional, it is the real snapshot): `~/stitch-private/demo-family/prepped/marmalade.jpg`

The agent asks: *"Who was in the kitchen when Marmalade came home?"* Answer:

> Just me and Mom. Mark heard about it on the phone that night.

**Props review:** keep all three (Marmalade, the cat basket, Jenna's water bottle). Removing one is fine too; the scene keeps any prop it needs.

It ends on the kitchen scene: Carol walks in with the kitten, hands Marmalade to Jenna. In the book, **click the kitten** for its story (click-to-explain), and pull the tabs ("Pull to let the kitten peek out", "Pull to hand Marmalade to Jenna").

## 2. Rick tells "Matching overalls on the lake" on the phone

The call itself only saves a **private draft** for Rick. The memory (and the replayed canoe scene) starts when Rick submits that draft through Add a memory. The prepared scene matches any telling with the word **"canoe"** in it, so a transcript that wanders still lands on it.

1. **Sign in as Rick** in a fresh window: `rick@macosko.demo` / `stitch-demo`. His account is linked to his own person, which is what lets the call land in his drafts.
2. **Get a call code:** open the call-pairing screen (Settings → notifications, or `/demo?family=macoskos`) and create a code. It is **three digits**, valid 15 minutes. Make a fresh one right before the call.
3. **Call the ElevenLabs agent.** When it asks, say **"STITCH"** followed by the three digits, on their own, before the story (for example "STITCH 4 1 7"). Then tell the story:

   > Linda sewed matching overalls for all six of us for the canoe trip, so nobody could get lost. We tipped the canoe anyway, and Katie still says it was the best day of the summer.

   Hang up when done.
4. **Review the draft:** back on the web as Rick, open his call drafts (the same pairing screen lists them). The transcript is there, private to Rick; fix any words if needed.
5. **Submit it through Add a memory:** use the draft to start Add a memory, set **When was this?** to `1984`, and submit.
6. The agent feed runs. It asks *"Who was the first one in the water?"*; answer:

   > Daniel. He was in the front of the canoe.

7. It ends on the lake scene: the family walks down to the red canoe, four climb in, and pulling **"Pull to tip the canoe"** tips it with a splash.

If the call is not available, type the same story on /add as Rick (year 1984) and it replays the same way.

The demo host must run the launcher with `--channels`, `STITCH_CHANNELS_ELEVENLABS=1`, `CHANNELS_SHARED_SECRET`, `ELEVENLABS_WEBHOOK_SECRET` and `ELEVENLABS_AGENT_IDS`, and the ElevenLabs post-call webhook must reach that host's API (see `channels/CALLS.md`).

Photo for the typed version: `~/stitch-private/demo-family/prepped/canoe.jpg`

## Run a typed story a second time

A story replays only as a new memory. To run it again, restart the launcher (the in-memory data resets on every start).
