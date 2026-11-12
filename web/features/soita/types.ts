import type { ReportStatus } from "@/lib/types";

/** GET /api/mail/recipients, as amended in ARCHITECTURE.md section 15.7. */
export interface MailRecipientsInfo {
  recipients: string[];
  default?: string | null;
  enabled: boolean;
}

/** GET /api/whatsapp/status (section 9). */
export interface WhatsAppStatus {
  active: boolean;
  last_poll_at: string | null;
  queued: number;
}

/** Section 15.7 adds `sending` between `needs_approval` and `sent`. */
export type BoardStatus = ReportStatus | "sending";

export type CallPhase = "idle" | "connecting" | "live" | "saving" | "saved";

export interface Caption {
  role: "agent" | "caller";
  text: string;
}
