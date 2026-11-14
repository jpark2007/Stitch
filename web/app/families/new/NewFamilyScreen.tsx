"use client";

// Start a family: signed-in only. The gate sends a signed-out visitor to
// /login?next=/families/new and brings them back. Someone who already has a
// family may start another, with a way back to the one they came from.
//
// After the family is made it becomes the remembered family and the page goes
// to its settings with ?welcome=1, which the settings page greets.
//
// People land here after signing up, or logging in with no family yet. Some
// of them were invited and only lost the link on the way, so under the form a
// quiet "Were you invited?" field takes a pasted invite link (or its code)
// and opens /join/<code>. "Not you? Sign out" is at the foot for a shared laptop.
//
// With cacheComponents on, router.push leaves this page mounted in a hidden
// <Activity> with the sent form still filled in, and "Start another family"
// shows it again. One more press would make a duplicate family. So a
// successful create is recorded (freshForm.ts) and, when the page is shown
// again, the form remounts empty under a new key. Not before the push: that
// would flash an empty form while leaving. A draft that was never sent is kept.
import { useRouter } from "next/navigation";
import { useLayoutEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/flow/Button";
import { CreateFamilyForm } from "@/components/flow/CreateFamilyForm";
import { Field, fieldControlClass } from "@/components/flow/Field";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { freshFormTracker } from "@/components/flow/freshForm";
import { parseInviteCode } from "@/components/flow/inviteLink";
import { hrefs, rememberFamily } from "@/components/flow/session";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { signedInLabel } from "@/components/flow/settingsPage";
import type { CreateFamilyRequest } from "@/lib/types";

/** Fixed with the settings page: present means "this family was just made". */
const WELCOME_QUERY = "?welcome=1";

const NOT_AN_INVITE = "That doesn't look like an invite link.";
const SIGN_OUT_ERROR = "We couldn't sign you out. Check your connection and try again.";

export function NewFamilyScreen() {
  return <SessionGate>{(session) => <NewFamily session={session} />}</SessionGate>;
}

function NewFamily(props: { session: SessionValue }) {
  const { me, api, refresh, signOut } = props.session;
  const router = useRouter();
  // One tracker for the life of this page; it holds no React state of its own.
  const [fresh] = useState(freshFormTracker);
  // Bumped to remount the form empty after a family was made from it.
  const [formKey, setFormKey] = useState(0);

  // Runs on mount and again each time the hidden page is shown. A layout
  // effect, so the sent draft is replaced before the page paints.
  useLayoutEffect(() => {
    // Remounting the form once per successful create, as the page reappears.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (fresh.takeReset()) setFormKey((k) => k + 1);
  }, [fresh]);

  async function create(body: CreateFamilyRequest) {
    const res = await api.createFamily(body);
    // The family exists now, whatever happens next: this draft is spent.
    fresh.markSubmitted();
    rememberFamily(res.family.id);
    try {
      await refresh();
    } catch {
      // The family exists; the settings page asks the server again on its own.
      // Failing here would invite a second submit and a duplicate family.
    }
    router.push(hrefs.settings(res.family.id) + WELCOME_QUERY);
  }

  const back =
    me.families.length > 0 ? { href: hrefs.home(), label: "Back to your family" } : undefined;

  return (
    <FlowPage
      title="Start your family"
      lede="You’ll be the first person in its tree and its admin. Next, you can invite everyone else."
      back={back}
    >
      <CreateFamilyForm key={formKey} defaultYourName={me.user.name} onSubmit={create} />
      <hr aria-hidden="true" className="stitch-rule mx-auto mt-10 w-24" />
      <OpenInvite onOpen={(code) => router.push(hrefs.join(code))} />
      <SignedInAs label={signedInLabel(me.user)} signOut={signOut} />
    </FlowPage>
  );
}

/** "Were you invited?": paste the link (or its code) and open it here. */
function OpenInvite(props: { onOpen: (code: string) => void }) {
  const { onOpen } = props;
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = parseInviteCode(value);
    if (!code) {
      setError(NOT_AN_INVITE);
      inputRef.current?.focus();
      return;
    }
    setError(null);
    onOpen(code);
  }

  return (
    <section aria-labelledby="invited-heading" className="mt-10">
      <h2 id="invited-heading" className="font-display text-[1.25rem] leading-snug text-ink">
        Were you invited?
      </h2>
      <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
        Open the link a relative sent you to join their family instead.
      </p>
      <form noValidate onSubmit={submit} className="mt-4">
        <Field label="Paste your invite link" error={error}>
          {(c) => (
            <div className="flex items-start gap-2">
              <input
                {...c}
                ref={inputRef}
                type="text"
                name="inviteLink"
                inputMode="url"
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  if (error) setError(null);
                }}
                className={fieldControlClass(c["aria-invalid"] === true, "min-w-0 flex-1")}
              />
              <Button type="submit" variant="secondary" className="shrink-0">
                Open
              </Button>
            </div>
          )}
        </Field>
      </form>
    </section>
  );
}

/** "Signed in as … Not you? Sign out", as on the invite page; signing out goes to /login. */
function SignedInAs(props: { label: string; signOut: () => Promise<void> }) {
  const { label, signOut } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function leave() {
    setError(null);
    setBusy(true);
    try {
      // Leaves the page with a full load on success, so busy stays on.
      await signOut();
    } catch {
      setError(SIGN_OUT_ERROR);
      setBusy(false);
    }
  }

  return (
    <div className="mt-10 text-center text-[0.875rem] leading-snug break-words text-ink-soft">
      <p>
        Signed in as {label}.{" "}
        <button
          type="button"
          onClick={leave}
          disabled={busy}
          className="cursor-pointer font-semibold text-accent-deep underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-deep disabled:cursor-wait disabled:opacity-60"
        >
          {busy ? "Signing out…" : "Not you? Sign out"}
        </button>
      </p>
      {error ? (
        <div className="mx-auto mt-3 max-w-[24rem] text-left">
          <FormError message={error} />
        </div>
      ) : null}
    </div>
  );
}
