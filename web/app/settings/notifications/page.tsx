import { Suspense } from "react";
import { NotificationSettings } from "@/components/channels/NotificationSettings";

export default function NotificationSettingsPage() {
  return <Suspense fallback={<p className="p-8 text-ink-soft">Opening contact settings…</p>}><NotificationSettings /></Suspense>;
}
