"use client";

// Prepared stories: tellings typed ahead of time, kept in this browser
// (localStorage; a per-operator convenience). Each can be copied, or started
// on the audience screen: the add page opens there with the telling, year
// and question filled in (via DRAFT_KEY and ?demoDraft=1), so the operator
// only presses submit and replay mode matches it word for word. Add, edit,
// delete, and move the library between laptops as JSON.
import { useEffect, useId, useReducer, useRef, useState } from "react";

import { audienceHrefs } from "./audience";
import {
  exportStories,
  importStories,
  inputFromStory,
  loadStories,
  newStoryId,
  saveStories,
  storiesReducer,
  storyErrors,
  storyFromInput,
  writeDraft,
  type PreparedStory,
  type StoryErrors,
  type StoryInput,
} from "./stories";
import { ActionLine, Chip, FIELD_ERROR, IDLE, INPUT, LABEL, Panel, TEXTAREA, type ActionState } from "./ui";

function local(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

type Mode = { kind: "list" } | { kind: "edit"; id: string | null } | { kind: "json" };

export function StoryLibrary(props: { fid: string; go: (url: string) => boolean }) {
  const { fid, go } = props;
  const [stories, dispatch] = useReducer(storiesReducer, null, () => loadStories(local()));
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [action, setAction] = useState<ActionState>(IDLE);
  const [saveFailed, setSaveFailed] = useState(false);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setSaveFailed(!saveStories(local(), stories));
  }, [stories]);

  async function copy(s: PreparedStory) {
    try {
      await navigator.clipboard.writeText(s.text);
      setAction({ kind: "done", text: `Copied “${s.title}”.` });
    } catch {
      setAction({ kind: "error", text: "The browser wouldn’t copy. Select the telling in Edit and copy it by hand." });
    }
  }

  function start(s: PreparedStory) {
    if (!writeDraft(local(), s)) {
      setAction({ kind: "error", text: "This browser’s storage is blocked, so the telling can’t be handed to the add page. Use Copy instead." });
      return;
    }
    const ok = go(audienceHrefs.addDraft(fid));
    setAction(
      ok
        ? { kind: "done", text: `“${s.title}” is filled in on the audience screen. Press “Save this memory” there when ready.` }
        : { kind: "error", text: "The browser blocked the audience window. Allow pop-ups for this site, then try again." },
    );
  }

  const editing = mode.kind === "edit" ? (mode.id ? stories.find((s) => s.id === mode.id) ?? null : null) : null;

  return (
    <Panel
      title="Prepared stories"
      kicker="Tell it on stage"
      aside={
        mode.kind === "list" ? (
          <span className="flex gap-1">
            <Chip tone="quiet" onClick={() => setMode({ kind: "json" })}>
              Import / export
            </Chip>
            <Chip onClick={() => setMode({ kind: "edit", id: null })}>Add a story</Chip>
          </span>
        ) : null
      }
    >
      {saveFailed ? (
        <p role="alert" className="mb-2 text-xs text-[#9c3b2a]">
          This browser isn’t keeping the library (storage is blocked). Export it before you leave the page.
        </p>
      ) : null}
      {mode.kind === "edit" ? (
        <StoryEditor
          key={mode.id ?? "new"}
          initial={editing}
          onCancel={() => setMode({ kind: "list" })}
          onSave={(s, isNew) => {
            dispatch({ type: isNew ? "add" : "update", story: s });
            setMode({ kind: "list" });
            setAction({ kind: "done", text: isNew ? `Added “${s.title}”.` : `Saved “${s.title}”.` });
          }}
        />
      ) : mode.kind === "json" ? (
        <StoryJson
          stories={stories}
          onImport={(imported, skipped) => {
            dispatch({ type: "import", stories: imported });
            setMode({ kind: "list" });
            setAction({
              kind: "done",
              text: `Imported ${imported.length} ${imported.length === 1 ? "story" : "stories"}${skipped ? `; skipped ${skipped} without a telling` : ""}. Any already in the library were left as they were.`,
            });
          }}
          onClose={() => setMode({ kind: "list" })}
        />
      ) : stories.length === 0 ? (
        <p className="text-sm leading-relaxed text-ink-soft">
          None yet. Add the tellings you will tell on stage, word for word as the prepared replay story expects, so
          one press fills them in on the audience screen.
        </p>
      ) : (
        <ul className="-mx-1 max-h-[16rem] list-none overflow-y-auto p-0">
          {stories.map((s) => (
            <StoryRow
              key={s.id}
              story={s}
              onStart={() => start(s)}
              onCopy={() => void copy(s)}
              onEdit={() => setMode({ kind: "edit", id: s.id })}
              onDelete={() => {
                dispatch({ type: "remove", id: s.id });
                setAction({ kind: "done", text: `Deleted “${s.title}”.` });
              }}
            />
          ))}
        </ul>
      )}
      <ActionLine state={action} className="mt-2" />
    </Panel>
  );
}

function StoryRow(props: { story: PreparedStory; onStart: () => void; onCopy: () => void; onEdit: () => void; onDelete: () => void }) {
  const { story: s } = props;
  const [confirming, setConfirming] = useState(false);
  return (
    <li className="rounded-card px-2 py-2 odd:bg-paper/60">
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-semibold text-ink">{s.title}</span>
        {s.year !== null ? <span className="shrink-0 text-sm tabular-nums text-ink-faint">{s.year}</span> : null}
      </div>
      <p className="mt-0.5 line-clamp-1 text-[0.8125rem] text-ink-soft">{s.question ? `“${s.question}” · ` : ""}{s.text}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {confirming ? (
          <>
            <span className="text-sm text-ink-soft">Delete this story?</span>
            <Chip tone="danger" onClick={props.onDelete}>
              Delete
            </Chip>
            <Chip tone="quiet" onClick={() => setConfirming(false)}>
              Keep it
            </Chip>
          </>
        ) : (
          <>
            <Chip tone="accent" onClick={props.onStart}>
              Start on audience screen
            </Chip>
            <Chip onClick={props.onCopy}>Copy</Chip>
            <Chip tone="quiet" onClick={props.onEdit}>
              Edit
            </Chip>
            <Chip tone="quiet" onClick={() => setConfirming(true)} aria-label={`Delete ${s.title}`}>
              Delete
            </Chip>
          </>
        )}
      </div>
    </li>
  );
}

function StoryEditor(props: { initial: PreparedStory | null; onSave: (s: PreparedStory, isNew: boolean) => void; onCancel: () => void }) {
  const { initial } = props;
  const [input, setInput] = useState<StoryInput>(() => inputFromStory(initial));
  const [errors, setErrors] = useState<StoryErrors>({});
  const id = useId();
  const set = (k: keyof StoryInput) => (e: { target: { value: string } }) => setInput((v) => ({ ...v, [k]: e.target.value }));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const found = storyErrors(input);
    setErrors(found);
    if (Object.keys(found).length) return;
    props.onSave(storyFromInput(input, initial?.id ?? newStoryId()), initial === null);
  }

  const field = (k: keyof StoryInput) => ({
    id: `${id}-${k}`,
    value: input[k],
    onChange: set(k),
    "aria-invalid": errors[k] ? true : undefined,
    "aria-describedby": errors[k] ? `${id}-${k}-error` : undefined,
  });
  const err = (k: keyof StoryInput) =>
    errors[k] ? (
      <p id={`${id}-${k}-error`} className={FIELD_ERROR}>
        {errors[k]}
      </p>
    ) : null;

  return (
    <form onSubmit={submit} noValidate className="space-y-3">
      <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-3">
        <div>
          <label htmlFor={`${id}-title`} className={LABEL}>
            Title
          </label>
          <input {...field("title")} className={INPUT} placeholder="How Grace and I met" autoComplete="off" />
          {err("title")}
        </div>
        <div>
          <label htmlFor={`${id}-year`} className={LABEL}>
            Year
          </label>
          <input {...field("year")} className={INPUT} inputMode="numeric" placeholder="1968" autoComplete="off" />
          {err("year")}
        </div>
      </div>
      <div>
        <label htmlFor={`${id}-question`} className={LABEL}>
          Question it answers <span className="font-normal">(optional)</span>
        </label>
        <input {...field("question")} className={INPUT} placeholder="How did you and Grace meet?" autoComplete="off" />
        {err("question")}
      </div>
      <div>
        <label htmlFor={`${id}-text`} className={LABEL}>
          The telling, word for word
        </label>
        <textarea {...field("text")} rows={5} className={TEXTAREA} placeholder="It was the summer of the county fair…" />
        {err("text")}
      </div>
      <div className="flex gap-2">
        <Chip type="submit" tone="accent">
          {initial ? "Save story" : "Add story"}
        </Chip>
        <Chip tone="quiet" onClick={props.onCancel}>
          Cancel
        </Chip>
      </div>
    </form>
  );
}

function StoryJson(props: { stories: PreparedStory[]; onImport: (s: PreparedStory[], skipped: number) => void; onClose: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<ActionState>(IDLE);
  const id = useId();
  const exported = exportStories(props.stories);

  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <label htmlFor={`${id}-export`} className={LABEL}>
            Export ({props.stories.length} {props.stories.length === 1 ? "story" : "stories"})
          </label>
          <Chip
            tone="quiet"
            onClick={() =>
              navigator.clipboard.writeText(exported).then(
                () => setCopied({ kind: "done", text: "Copied the library as JSON." }),
                () => setCopied({ kind: "error", text: "The browser wouldn’t copy. Select the text and copy it by hand." }),
              )
            }
          >
            Copy JSON
          </Chip>
        </div>
        <textarea id={`${id}-export`} readOnly rows={4} value={exported} className={`${TEXTAREA} font-mono text-xs!`} />
        <ActionLine state={copied} className="mt-1" />
      </div>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const r = importStories(text);
          if (!r.ok) {
            setError(r.error);
            return;
          }
          props.onImport(r.stories, r.skipped);
        }}
      >
        <label htmlFor={`${id}-import`} className={LABEL}>
          Import: paste JSON from another laptop
        </label>
        <textarea
          id={`${id}-import`}
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-import-error` : undefined}
          className={`${TEXTAREA} font-mono text-xs!`}
          placeholder='{"stories": [{"title": "…", "year": 1968, "text": "…", "question": "…"}]}'
        />
        {error ? (
          <p id={`${id}-import-error`} role="alert" className={FIELD_ERROR}>
            {error}
          </p>
        ) : null}
        <div className="mt-2 flex gap-2">
          <Chip type="submit" tone="accent">
            Import
          </Chip>
          <Chip tone="quiet" onClick={props.onClose}>
            Done
          </Chip>
        </div>
      </form>
    </div>
  );
}
