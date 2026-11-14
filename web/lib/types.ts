// GENERATED FILE. Do not edit by hand.
// Source: api/schemas.py and api/screenplay/schema.py
// Regenerate: cd api && uv run python ../shared/gen_types.py
// Conventions (axes, units, channels): shared/CONTRACTS.md

export interface Action {
  /** Seconds from shot start */
  t: number;
  do: "walk_to" | "run_to" | "enter" | "exit" | "turn_to" | "follow" | "pose" | "wave" | "point_at" | "sit_on" | "stand_up" | "kneel" | "hug" | "lean_toward" | "pick_up" | "hold" | "drop" | "hand_to" | "put_on" | "place_on" | "open" | "close" | "set_state" | "get_in" | "get_out" | "drive_to" | "ride_to" | "confetti" | "steam" | "rain" | "smoke" | "time_of_day";
  /** Optional name so narration cues can point at this action */
  id?: string | null;
  /** Person id */
  who?: string | null;
  /** Mark (movement) or person id (hand_to) */
  to?: string | null;
  from?: string | null;
  /** Prop id */
  what?: string | null;
  /** Person id, prop id or mark */
  target?: string | null;
  /** Prop id or mark to place on */
  on?: string | null;
  socket?: string | null;
  pose?: string | null;
  state?: string | null;
  /** Effects: mark, person or prop */
  at?: string | null;
  /** time_of_day value */
  value?: string | null;
  dur?: number | null;
}

/** POST /families/{fid}/runs/{id}/answer. Resumes the paused run. */
export interface AnswerRequest {
  kind: "answer" | "props" | "merge";
  /** kind=answer: the reply to the follow-up question */
  text?: string | null;
  /** kind=props: the reviewed prop list */
  props?: PropEdit[] | null;
  /** kind=merge: whether the two people are the same */
  approve?: boolean | null;
}

/** The single memory an ask points at: the one to open. */
export interface AskBest {
  memoryId: string;
  title?: string | null;
  year?: number | null;
  /** A person whose book holds it: the teller of the quoted telling, else the first participant */
  personId?: string | null;
  /** True only when this memory clearly leads the other candidates */
  confident: boolean;
}

export interface AskQuote {
  memoryId: string;
  authorId: string;
  text: string;
  title?: string | null;
  year?: number | null;
}

export interface AskRequest {
  query: string;
}

export interface AskResponse {
  answer: string;
  quotes: AskQuote[];
  /** The single memory to open; null when nothing fits */
  best?: AskBest | null;
  /** How it was answered */
  mode?: "search" | "model" | null;
}

export interface BookResponse {
  person: Person;
  /** Ordered by year */
  memories: Memory[];
  /** Every prop the memories reference */
  props?: Prop[];
  /** Every set the memories reference */
  sets?: SetDef[];
  /** Everyone who appears in the memories */
  people?: Person[];
}

export interface CameraCue {
  t: number;
  move: "wide" | "dolly_to" | "pan_to" | "close_up" | "pull_back";
  /** Person id, prop id or mark */
  target?: string | null;
  dur?: number | null;
}

export interface Caption {
  t: number;
  text: string;
}

export interface CharacterSheet {
  /** Hex body color. Also the person's thread color on the threads screen. */
  color: string;
  /** Signature item, e.g. "blue flat cap" */
  item?: string | null;
  build?: "small" | "short" | "average" | "tall";
  /** Prop worn in the head socket */
  itemPropId?: string | null;
  /** How they move, e.g. "walks slowly with a cane" */
  notes?: string | null;
}

/** Exact, machine-readable compiler error fed back to the director. */
export interface CompileError {
  /** Stable id, e.g. "not_holding", "unknown_mark", "out_of_reach" */
  code: string;
  /** e.g. shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it */
  message: string;
  shot?: string | null;
  t?: number | null;
  who?: string | null;
  do?: string | null;
}

export interface CompileErrorEvent {
  errors: CompileError[];
  round?: number;
}

export interface CompileResult {
  ok: boolean;
  timeline?: Timeline | null;
  errors?: CompileError[];
}

export interface CreateFamilyRequest {
  name: string;
  /** The creator's own person, the first node in the tree */
  person: NewPerson;
}

export interface CreateFamilyResponse {
  family: FamilySummary;
  person: Person;
}

/** POST /families/{fid}/films -> 202 Film. Bounds (422 when broken): title at most 200 characters after trimming (blank or omitted = the server picks one); text 1-20,000 after trimming; year from 1800 to this year; 8-40 photoIds (bare media ids already uploaded to this family); at most 100 participantIds. */
export interface CreateFilmRequest {
  title?: string | null;
  text: string;
  year: number;
  photoIds: string[];
  participantIds?: string[];
}

/** POST /families/{fid}/traditions. Bounds (422 when broken): title 1-200 characters after trimming; description at most 5,000 (may be empty); when.label at most 100; when.month 1-12; at most 100 participantIds and 10 photoIds; startedYear from 1800 to this year. */
export interface CreateTraditionRequest {
  title: string;
  description?: string;
  category: "food" | "holiday" | "trip" | "music" | "game" | "faith" | "craft" | "other";
  when: TraditionWhen;
  participantIds?: string[];
  photoIds?: string[];
  startedYear?: number | null;
}

export interface DoneEvent {
  memoryId: string;
}

export interface ErrorEvent {
  message: string;
}

export interface ErrorResponse {
  detail: string;
}

/** Click-to-explain text for one cast member or prop. */
export interface Explanation {
  /** "puppet:<personId>" or "prop:<propId>" */
  target: string;
  text: string;
}

export interface Family {
  id: string;
  name: string;
  members?: Member[];
  invites?: Invite[];
}

/** One family the current user belongs to. */
export interface FamilySummary {
  id: string;
  name: string;
  role: "admin" | "member";
  /** The caller's person in this family */
  personId?: string | null;
}

/** A photo film. Every field is always sent: shots, beats and words are empty and duration is 0 until status is "ready". */
export interface Film {
  id: string;
  familyId: string;
  /** Person id of the teller */
  authorId: string;
  title: string;
  year: number;
  /** The teller's story, as written */
  text: string;
  /** Person ids of the people in it */
  participantIds: string[];
  status: "processing" | "ready" | "failed";
  /** A safe message for people when status is failed, else null */
  error: string | null;
  /** Every photo uploaded for it (bare media ids), chosen or not */
  photoIds: string[];
  /** In play order */
  shots: FilmShot[];
  beats: FilmBeat[];
  /** Narrated words with their times, for captions */
  words: FilmWord[];
  /** Narration 'media/<id>'; null = silent, play on a clock */
  audio: string | null;
  /** Seconds; 0 until ready */
  duration: number;
  createdAt: string;
}

/** One beat of the story: a run of shots under one narration line. */
export interface FilmBeat {
  title: string;
  /** Seconds from the start of the film */
  start: number;
  /** The line spoken over this beat, in the teller's words */
  narration: string;
}

/** One photo on screen: a slow pan/zoom toward `focus` for `duration` seconds. */
export interface FilmShot {
  /** Bare media id (as returned by POST /families/{fid}/media) */
  photoId: string;
  /** Seconds from the start of the film */
  start: number;
  /** Seconds on screen, crossfades included */
  duration: number;
  /** Index into Film.beats */
  beat: number;
  /** Handwritten caption, if any */
  caption?: string | null;
  /** [u, v]: fraction across and down the photo the motion moves toward */
  focus?: [number, number];
  /** in/out = zoom, left/right = pan */
  motion?: "in" | "out" | "left" | "right";
}

/** A film without its cut, for lists. */
export interface FilmSummary {
  id: string;
  title: string;
  year: number;
  authorId: string;
  status: "processing" | "ready" | "failed";
  /** Seconds; 0 until ready */
  duration: number;
  /** Bare media id of the first shot (or photo) */
  coverPhotoId?: string | null;
  createdAt: string;
}

/** One narrated word and when it is spoken (the Timeline's word timing, in film time). */
export interface FilmWord {
  /** Seconds from the start of the film */
  t: number;
  word: string;
}

/** GET /families/{fid}/films: every film in the family, newest first. */
export interface FilmsResponse {
  films: FilmSummary[];
}

export interface FrameShot {
  t: number;
  mediaId: string;
}

export interface FramesEvent {
  frames: FrameShot[];
  critique?: string | null;
}

export interface Interaction {
  id: string;
  kind: "pull_tab" | "slider";
  /** Printed next to the tab, e.g. "Pull to drive the car" */
  label: string;
  /** Shot id it belongs to; default every shot */
  shot?: string | null;
  page?: "stage" | "story";
  /** [u, v] position on that page, fractions from top-left */
  at: number[];
  drives: InteractionDrive;
}

/** What a pull tab or slider controls. Exactly one style, chosen by `type`. */
export interface InteractionDrive {
  type: "scrub" | "prop_state" | "prop_part";
  from?: number | null;
  to?: number | null;
  /** Track targets to scrub, e.g. ["prop:hymn_books"]; default all */
  targets?: string[] | null;
  prop?: string | null;
  states?: string[] | null;
  part?: string | null;
  channel?: "rot" | "offset" | null;
  /** [min, max] for the part channel */
  range?: number[] | null;
}

export interface Invite {
  code: string;
  createdBy: string;
  expiresAt: string;
  revoked?: boolean;
}

export interface InvitePreview {
  familyId: string;
  familyName: string;
  unclaimedPeople: PersonSummary[];
}

export interface InviteResponse {
  code: string;
  /** "/join/<code>" */
  path: string;
  expiresAt: string;
}

/** Exactly one of person_id (claim) or person (add yourself). */
export interface JoinRequest {
  personId?: string | null;
  person?: NewPerson | null;
}

export interface JoinResponse {
  family: FamilySummary;
  person: Person;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface MeResponse {
  user: User;
  families: FamilySummary[];
}

/** POST /families/{fid}/media (multipart field `file`). */
export interface MediaUploadResponse {
  /** Bare media id; reference it as 'media/<id>' or pass it as a photo id */
  id: string;
}

export interface Member {
  userId: string;
  role: "admin" | "member";
  joinedAt?: string | null;
}

/** PATCH /families/{fid}/members/{userId}. Admin only; the last admin cannot be demoted. */
export interface MemberRolePatch {
  role: "admin" | "member";
}

/** A moment: one real-world event, shared across the books of everyone in it. */
export interface Memory {
  id: string;
  familyId: string;
  title: string;
  year: number;
  /** Era id from shared/vocabulary.json, derived from year */
  era?: string | null;
  participantIds?: string[];
  tellings?: Telling[];
  screenplay?: Screenplay | null;
  timeline?: Timeline | null;
  propIds?: string[];
  setIds?: string[];
  version?: number;
}

export interface MemoryResponse {
  memory: Memory;
  props?: Prop[];
  sets?: SetDef[];
  people?: Person[];
}

/** Needs approval: answer with AnswerRequest(kind="merge"). */
export interface MergeEvent {
  text: string;
  personId: string;
  candidateName: string;
}

/** A moment without its scene, for the threads screen and lists. */
export interface MomentSummary {
  id: string;
  title: string;
  year: number;
  participantIds: string[];
  era?: string | null;
  tellingAuthorIds?: string[];
}

export interface NarrationLine {
  /** Screenplay: seconds from shot start. Timeline: seconds from timeline start. */
  t: number;
  text: string;
  /** Where the words come from, e.g. "telling:dad:s2" */
  source?: string | null;
  /** Action this line is timed to: an action id, else the first action in the shot whose `do` equals it */
  cue?: string | null;
  /** The word the cued action lands on (default: the last word) */
  cueWord?: string | null;
  /** Word timestamps from TTS, timeline time */
  words?: WordTiming[] | null;
}

/** One telling's narration over the shared action. */
export interface NarrationTrack {
  authorId: string;
  /** Media id of the narration audio */
  audio?: string | null;
  captions?: Caption[];
  lines?: NarrationLine[];
}

export interface NewPerson {
  name: string;
  born?: number | null;
  died?: number | null;
  parentIds?: string[];
  spouseIds?: string[];
  /** Picked automatically when omitted */
  character?: CharacterSheet | null;
}

export interface Person {
  id: string;
  familyId: string;
  /** null until someone claims this person */
  userId?: string | null;
  name: string;
  born?: number | null;
  died?: number | null;
  parentIds?: string[];
  spouseIds?: string[];
  character: CharacterSheet;
  addedBy?: string | null;
}

export interface PersonPatch {
  name?: string | null;
  born?: number | null;
  died?: number | null;
  parentIds?: string[] | null;
  spouseIds?: string[] | null;
  character?: CharacterSheet | null;
}

export interface PersonSummary {
  id: string;
  name: string;
  born?: number | null;
  color?: string | null;
}

export interface PlanEvent {
  todos: PlanItem[];
}

export interface PlanItem {
  text: string;
  status?: "pending" | "in_progress" | "completed";
}

/** A saved prop and its rig (ARCHITECTURE.md section 1). */
export interface Prop {
  id: string;
  /** null for built-in library props */
  familyId?: string | null;
  name: string;
  description?: string | null;
  source?: "library" | "generated" | "photo";
  size: "tiny" | "hand" | "carried" | "furniture" | "vehicle" | "building";
  /** Art key -> media id, e.g. {"body": "media/a1"}. null = placeholder card. */
  art: Record<string, string | null>;
  /** Placeholder color when art is missing */
  color?: string | null;
  /** Image width / height */
  aspect?: number;
  /** Where it meets the ground, image fraction */
  anchor?: number[];
  /** Where a hand holds it, image fraction */
  grip?: number[] | null;
  sockets?: Record<string, number[]>;
  parts?: Record<string, PropPart>;
  /** State name -> art overrides (art key -> media id). {} = no visual change. */
  states?: Record<string, Record<string, string>>;
}

/** A prop the agent found, shown for family review. */
export interface PropChip {
  id: string;
  name: string;
  description?: string | null;
  size: "tiny" | "hand" | "carried" | "furniture" | "vehicle" | "building";
  /** Media id of a preview */
  art?: string | null;
  reused?: boolean;
}

export interface PropEdit {
  id: string;
  action: "keep" | "rename" | "remove" | "replace_photo";
  name?: string | null;
  photoId?: string | null;
}

export interface PropPart {
  /** Key into the prop's `art` map */
  art: string;
  /** [u, v] fraction of the prop image */
  at: number[];
  spin?: boolean;
}

export interface PropPatch {
  name?: string | null;
  /** Replace the art with an uploaded photo of the real thing */
  photoId?: string | null;
}

export interface PropStart {
  /** Mark where the prop stands at shot start */
  at?: string | null;
  state?: string | null;
  /** Resting on "<propId>.<socket>" */
  on?: string | null;
}

/** Needs review: answer with AnswerRequest(kind="props"). */
export interface PropsFoundEvent {
  props: PropChip[];
}

export interface PropsResponse {
  props: Prop[];
}

export interface Question {
  id: string;
  familyId: string;
  personId: string;
  text: string;
  memoryId?: string | null;
  status?: "open" | "answered" | "dismissed";
}

/** Needs input: answer with AnswerRequest(kind="answer"). */
export interface QuestionEvent {
  text: string;
}

export interface QuestionsResponse {
  questions: Question[];
}

export interface SceneReadyEvent {
  memoryId: string;
  version?: number;
}

export interface Screenplay {
  memoryId: string;
  /** Person ids */
  cast: string[];
  /** Prop ids */
  props?: string[];
  shots: Shot[];
  /** Other tellings: authorId -> shotId -> narration lines. The action is shared. */
  narrations?: Record<string, Record<string, NarrationLine[]>> | null;
  interactions?: Interaction[] | null;
  explanations?: Explanation[] | null;
}

/** GET /session: who is signed in. Always 200; `user` is null when nobody is. */
export interface SessionResponse {
  user?: User | null;
  families?: FamilySummary[];
}

/** A saved place: paper layers plus named marks. */
export interface SetDef {
  id: string;
  familyId?: string | null;
  name: string;
  description?: string | null;
  layers: SetLayer[];
  marks?: Record<string, number[]>;
}

export interface SetLayer {
  /** Media id of the cutout. null = placeholder card. */
  art: string | null;
  /** 0 = back, 1 = middle, 2 = front. Stage z = depth * 0.35. */
  depth: number;
  /** Height in stage units when risen */
  height: number;
  /** Placeholder color when art is missing */
  color?: string | null;
}

export interface SetsResponse {
  sets: SetDef[];
}

export interface Shot {
  id: string;
  /** Set id */
  set: string;
  /** Extra or overriding marks for this shot */
  marks?: Record<string, number[]>;
  /** Person id -> where they begin */
  start?: Record<string, StartState>;
  /** Prop id -> where it begins if not held */
  propStart?: Record<string, PropStart>;
  camera?: CameraCue[];
  actions?: Action[];
  /** The default telling's narration */
  narration?: NarrationLine[];
  transition?: "fold" | "none";
  /** Short storybook text printed on the page for this shot */
  pageText?: string | null;
}

export interface SignupRequest {
  email: string;
  password: string;
  name: string;
}

export interface StartState {
  /** Mark name, or offstage_left / offstage_right */
  at: string;
  /** Prop id held at shot start */
  holding?: string | null;
  pose?: string | null;
  facing?: "left" | "right" | null;
  /** Seated in "<propId>.<socket>" */
  in?: string | null;
}

export interface SubagentEndEvent {
  name: string;
  summary?: string | null;
}

export interface SubagentStartEvent {
  name: string;
  task?: string | null;
}

/** Form fields of POST /families/{fid}/memories (multipart; photos are files named `photos`). */
export interface SubmitMemoryFields {
  text: string;
  year: number;
  question?: string | null;
  title?: string | null;
  /** Set to add a second telling to an existing moment */
  memoryId?: string | null;
  participantIds?: string[];
  /** Media id of the teller's own recording (same family), saved on the telling */
  audioId?: string | null;
  /** How the telling arrived, saved on the telling */
  channel?: "web" | "text" | "voice" | null;
}

export interface SubmitMemoryResponse {
  runId: string;
  memoryId?: string | null;
}

/** One person's answer about a moment. */
export interface Telling {
  /** Person id */
  authorId: string;
  question?: string | null;
  text: string;
  photoIds?: string[];
  createdAt?: string | null;
  /** Media id of the teller's own recording */
  audioId?: string | null;
  /** How the telling arrived */
  channel?: "web" | "text" | "voice" | null;
}

/** GET /families/{fid}/threads: every moment in the family, ordered by year. */
export interface ThreadsResponse {
  people: PersonSummary[];
  moments: MomentSummary[];
}

export interface Timeline {
  duration: number;
  /** Media id for the default telling's narration */
  audio?: string | null;
  tracks: Track[];
  /** The default telling's captions */
  captions?: Caption[];
  shots?: TimelineShot[];
  effects?: TimelineEffect[];
  /** authorId -> narration track. Switching teller swaps audio and captions only. */
  narrations?: Record<string, NarrationTrack> | null;
  /** authorId whose narration is `audio`/`captions` */
  defaultNarrator?: string | null;
  /** true when root.position.y already includes foot planting for walk/run keys; the stage must then not plant feet itself */
  feetPlanted?: boolean | null;
  interactions?: Interaction[] | null;
  explanations?: Explanation[] | null;
}

export interface TimelineEffect {
  t: number;
  kind: "confetti" | "steam" | "rain" | "smoke" | "time_of_day";
  /** Stage position [x, y, z] */
  at?: number[] | null;
  dur?: number | null;
  value?: string | null;
}

export interface TimelineShot {
  id: string;
  set: string;
  /** When this shot's set begins to rise */
  start: number;
  /** When screenplay t=0 of this shot happens */
  actionStart: number;
  end: number;
  transition?: "fold" | "none";
  pageText?: string | null;
}

export interface Track {
  /** "puppet:<id>", "prop:<id>", "set:<id>.layer<n>" or "camera" */
  target: string;
  /** See the channel table in shared/CONTRACTS.md */
  channel: string;
  /** [t, value] pairs, sorted by t */
  keys: ([number, number | number[] | string | null])[];
}

/** Something the family does again and again: one page in the Book of Family Traditions. */
export interface Tradition {
  id: string;
  familyId: string;
  title: string;
  /** May be empty */
  description: string;
  category: "food" | "holiday" | "trip" | "music" | "game" | "faith" | "craft" | "other";
  when: TraditionWhen;
  /** Person ids of the people who keep it */
  participantIds: string[];
  /** Media ids (as returned by POST /families/{fid}/media) */
  photoIds: string[];
  /** The year it began, when known */
  startedYear?: number | null;
  /** User id of whoever added it */
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

/** When a tradition happens. `label` is free text ("Christmas Eve", "every Sunday"), at most 100 characters after trimming; `month` is 1-12 for the month it happens in, if any. */
export interface TraditionWhen {
  kind: "yearly" | "seasonal" | "monthly" | "weekly" | "occasional";
  /** e.g. "Christmas Eve", "every Sunday" */
  label?: string | null;
  /** 1 = January ... 12 = December */
  month?: number | null;
}

/** GET /families/{fid}/traditions: every tradition in the family. */
export interface TraditionsResponse {
  traditions: Tradition[];
}

export interface TreeResponse {
  familyId: string;
  familyName: string;
  people: Person[];
}

/** PATCH /families/{fid}/traditions/{id}. Every field is optional; an omitted field is left as it is and `when` is replaced whole. Send `startedYear: null` to clear it (the route tells null from omitted with `model_fields_set`). Same bounds as CreateTraditionRequest. */
export interface UpdateTraditionRequest {
  title?: string | null;
  description?: string | null;
  category?: "food" | "holiday" | "trip" | "music" | "game" | "faith" | "craft" | "other" | null;
  when?: TraditionWhen | null;
  participantIds?: string[] | null;
  photoIds?: string[] | null;
  startedYear?: number | null;
}

/** A login. Safe to return: there is deliberately no password field. */
export interface User {
  id: string;
  email: string;
  name: string;
  createdAt?: string | null;
}

export interface WordTiming {
  /** Seconds from the start of the timeline */
  t: number;
  word: string;
}

// From shared/vocabulary.json
export type ActionVerb = "walk_to" | "run_to" | "enter" | "exit" | "turn_to" | "follow" | "pose" | "wave" | "point_at" | "sit_on" | "stand_up" | "kneel" | "hug" | "lean_toward" | "pick_up" | "hold" | "drop" | "hand_to" | "put_on" | "place_on" | "open" | "close" | "set_state" | "get_in" | "get_out" | "drive_to" | "ride_to" | "confetti" | "steam" | "rain" | "smoke" | "time_of_day";
export type CameraMove = "wide" | "dolly_to" | "pan_to" | "close_up" | "pull_back";
export type EffectKind = "confetti" | "steam" | "rain" | "smoke" | "time_of_day";
export type Transition = "fold" | "none";
export type PropSize = "tiny" | "hand" | "carried" | "furniture" | "vehicle" | "building";
export type Build = "small" | "short" | "average" | "tall";
export type InteractionKind = "pull_tab" | "slider";

// From api/schemas.py
export type TraditionCategory = "food" | "holiday" | "trip" | "music" | "game" | "faith" | "craft" | "other";
export type TraditionWhenKind = "yearly" | "seasonal" | "monthly" | "weekly" | "occasional";

/** A mark is [u, h] or [u, h, d]. See shared/CONTRACTS.md. */
export type Mark = number[];
/** One timeline key: [time in seconds, value]. */
export type Key = [number, number | number[] | string | null];

/** SSE event name -> payload type for GET /families/{fid}/runs/{id}/stream */
export interface RunEvents {
  plan: PlanEvent;
  subagent_start: SubagentStartEvent;
  subagent_end: SubagentEndEvent;
  question: QuestionEvent;
  props_found: PropsFoundEvent;
  merge: MergeEvent;
  compile_error: CompileErrorEvent;
  frames: FramesEvent;
  scene_ready: SceneReadyEvent;
  done: DoneEvent;
  error: ErrorEvent;
}
