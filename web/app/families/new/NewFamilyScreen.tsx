"use client";

// Start a family: signed-in only. The gate sends a signed-out visitor to
// /login?next=/families/new and brings them back. Someone who already has a
// family may start another, with a way back to the one they came from.
//
// After the family is made it becomes the remembered family and the page goes
// to its settings with ?welcome=1, which the settings page greets.
import { useRouter } from "next/navigation";

import { CreateFamilyForm } from "@/components/flow/CreateFamilyForm";
import { FlowPage } from "@/components/flow/FlowPage";
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

  async function create(body: CreateFamilyRequest) {
    const res = await api.createFamily(body);
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
      lede="You’ll be its admin, so you can invite everyone else next."
      back={back}
    >
      <CreateFamilyForm defaultYourName={me.user.name} onSubmit={create} />
    </FlowPage>
  );
}
