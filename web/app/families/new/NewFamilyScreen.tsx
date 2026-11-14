"use client";

// Start a family: signed-in only. The gate sends a signed-out visitor to
// /login?next=/families/new and brings them back. Someone who already has a
// family may start another, with a way back to the one they came from.
//
// After the family is made it becomes the remembered family and the page goes
// to its settings with ?welcome=1, which the settings page greets.
//
// With cacheComponents on, router.push leaves this page mounted in a hidden
// <Activity> with the sent form still filled in, and "Start another family"
// shows it again. One more press would make a duplicate family. So a
// successful create is recorded (freshForm.ts) and, when the page is shown
// again, the form remounts empty under a new key. Not before the push: that
// would flash an empty form while leaving. A draft that was never sent is kept.
import { useRouter } from "next/navigation";
import { useLayoutEffect, useState } from "react";

import { CreateFamilyForm } from "@/components/flow/CreateFamilyForm";
import { FlowPage } from "@/components/flow/FlowPage";
import { freshFormTracker } from "@/components/flow/freshForm";
import { hrefs, rememberFamily } from "@/components/flow/session";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import type { CreateFamilyRequest } from "@/lib/types";

/** Fixed with the settings page: present means "this family was just made". */
const WELCOME_QUERY = "?welcome=1";

export function NewFamilyScreen() {
  return <SessionGate>{(session) => <NewFamily session={session} />}</SessionGate>;
}

function NewFamily(props: { session: SessionValue }) {
  const { me, api, refresh } = props.session;
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
    </FlowPage>
  );
}
