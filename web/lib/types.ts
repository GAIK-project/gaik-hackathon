/**
 * Shared wire types. Mirrors docs/ARCHITECTURE.md sections 7, 8, 9 and 10 one to one
 * (`server/app/core/models.py` is the Python side). Field names are snake_case as on the wire.
 *
 * Feature owners add their own types in `web/features/<demo>/types.ts`; do not change these.
 */

// ── Section 4: errors ────────────────────────────────────────────────────────

export interface ApiErrorBody {
  error: { code: string; message: string };
}

// ── Section 7: shared types ──────────────────────────────────────────────────

export type Channel = "call" | "whatsapp" | "upload" | "text" | "mcp";
export type ReportStatus = "live" | "processing" | "needs_approval" | "sent" | "failed";

export interface FieldDef {
  /** snake_case key */
  name: string;
  /** Finnish label */
  label: string;
  type: "text" | "longtext" | "date" | "time" | "number" | "choice";
  required: boolean;
  /** What the value means; goes to the extractor and the voice agent. */
  description: string;
  /** For "choice". */
  options?: string[];
}

export interface ReportType {
  /** "turvallisuushavainto" | "tyomaapaivakirja" | "huoltoraportti" | user-made */
  id: string;
  title: string;
  description: string;
  /** lucide icon name */
  icon: string;
  /** The plain-language requirement the fields were generated from. */
  requirement: string;
  fields: FieldDef[];
  builtin: boolean;
}

export interface FieldValue {
  name: string;
  label: string;
  value: string | null;
  /** The words in the transcript the value rests on, when available. */
  evidence: string | null;
  source: "voice" | "extractor" | "edit";
}

export interface TimelineEvent {
  id: string;
  at: string;
  kind:
    | "call"
    | "transcribe"
    | "classify"
    | "extract"
    | "urgency"
    | "document"
    | "email"
    | "approve"
    | "edit"
    | "error";
  /** "Kentät poimittu 11/12" */
  label: string;
  /** "Puuttuu: sääolosuhteet" */
  detail: string | null;
  /** gaik component or service behind the step, e.g. "extractor" */
  component: string | null;
  status: "running" | "done" | "waiting" | "error";
  ms: number | null;
}

export interface Urgency {
  label: "kiireellinen" | "normaali" | "ei kiireellinen";
  /** 0..1 */
  confidence: number;
  probabilities: Record<string, number>;
  model: string;
}

export interface ReportSummary {
  id: string;
  created_at: string;
  updated_at: string;
  channel: Channel;
  /** "Selainpuhelu", "WhatsApp", never a phone number */
  caller_label: string;
  type_id: string;
  type_title: string;
  status: ReportStatus;
  filled: number;
  /** Required fields. */
  total: number;
  urgency: Urgency | null;
  /** One line built from the most telling field. */
  headline: string;
}

export interface TranscriptTurn {
  role: "agent" | "caller";
  text: string;
  at: string;
}

export interface ReportFile {
  name: string;
  kind: "docx" | "audio";
  size: number;
  url: string;
}

export interface ReportEmail {
  to: string | null;
  subject: string;
  body: string;
  status: "draft" | "sent" | "failed";
  sent_at: string | null;
}

export interface Report extends ReportSummary {
  fields: FieldValue[];
  /** Labels of required fields still empty. */
  missing: string[];
  transcript: TranscriptTurn[];
  timeline: TimelineEvent[];
  files: ReportFile[];
  email: ReportEmail | null;
}

// ── Section 8.1: reports ─────────────────────────────────────────────────────

/** POST /api/report-types */
export interface CreateReportTypeRequest {
  requirement: string;
}

/** PATCH /api/reports/{id}/fields */
export interface UpdateFieldsRequest {
  values: Record<string, string | null>;
}

/** POST /api/reports/{id}/approve */
export interface ApproveReportRequest {
  to?: string;
}

/** POST /api/reports/{id}/correct (JSON form; the audio form is multipart `file`) */
export interface CorrectReportRequest {
  text: string;
}

/** GET /api/mail/recipients */
export interface MailRecipients {
  recipients: string[];
  enabled: boolean;
}

/** POST /api/intake/text */
export interface IntakeTextRequest {
  text: string;
  type_id?: string;
  channel?: Channel;
  caller_label?: string;
}

/** POST /api/intake/audio and /api/intake/text */
export interface IntakeResult {
  report: Report;
  /** Two-sentence Finnish summary for the sender. */
  reply: string;
}

/** GET /api/reports/stream */
export interface ReportsStreamEvents {
  report: ReportSummary;
}

/** GET /api/reports/{id}/stream */
export interface ReportStreamEvents {
  report: Report;
}

// ── Section 8.2: voice call (WS /api/call/ws) ────────────────────────────────

export type CallLanguage = "fi" | "en" | "sv";
export type CallState = "listening" | "thinking" | "speaking" | "saving" | "done";

/** Text frames the client sends. Microphone audio goes as binary frames (PCM16 LE, mono, 24 kHz). */
export type CallClientMessage =
  | { type: "start"; type_id: string; language: CallLanguage }
  | { type: "text"; text: string }
  | { type: "stop" };

/** Text frames the server sends. Agent speech comes as binary frames in the same PCM format. */
export type CallServerMessage =
  | { type: "ready"; report_id: string; report_type: ReportType }
  | { type: "state"; value: CallState }
  | { type: "transcript"; role: "agent" | "caller"; text: string; final: boolean; item_id: string }
  | {
      type: "fields";
      values: Record<string, string>;
      filled: number;
      total: number;
      asking: string | null;
    }
  | { type: "interrupt" }
  | { type: "saved"; report_id: string }
  | { type: "error"; message: string };

/** WebSocket close code the server uses when the session cookie is missing. */
export const WS_CLOSE_UNAUTHORIZED = 4401;

// ── Section 8.3: Studio ──────────────────────────────────────────────────────

export type StudioTemplate =
  | "documents_to_table"
  | "audio_to_report"
  | "documents_to_answers"
  | "documents_to_labels";

export interface StudioStep {
  id: string;
  component: string;
  title: string;
  detail: string;
  gaik_import: string;
}

export interface StudioInput {
  kind: "documents" | "audio";
  accept: string[];
  multiple: boolean;
  hint: string;
  sample_ids: string[];
}

export interface StudioClass {
  name: string;
  description: string;
}

export interface StudioSpec {
  id: string;
  /** "Laskut taulukoksi" */
  title: string;
  /** One sentence. */
  summary: string;
  /** The user's words. */
  requirement: string;
  template: StudioTemplate;
  input: StudioInput;
  /** The pipeline, in order. */
  steps: StudioStep[];
  /** For table and report templates. */
  fields: FieldDef[];
  /** For the labels template. */
  classes: StudioClass[];
  /** For the answers template. */
  question_examples: string[];
  output: { kind: "table" | "report" | "answer" | "labels" };
}

export interface StudioCatalogComponent {
  id: string;
  title: string;
  purpose: string;
  gaik_import: string;
  extra: string;
}

export interface StudioCatalogTemplate {
  id: StudioTemplate;
  title: string;
  steps: string[];
}

/** GET /api/studio/catalog */
export interface StudioCatalog {
  components: StudioCatalogComponent[];
  templates: StudioCatalogTemplate[];
}

export interface StudioSampleFile {
  name: string;
  url: string;
  size: number;
}

/** GET /api/studio/samples returns StudioSample[] */
export interface StudioSample {
  id: string;
  title: string;
  kind: string;
  files: StudioSampleFile[];
}

/** POST /api/studio/generate */
export interface StudioGenerateRequest {
  prompt: string;
}

/** SSE events of POST /api/studio/generate */
export interface StudioGenerateEvents {
  status: { text: string };
  step: { step: StudioStep };
  fields: { fields: FieldDef[] };
  classes: { classes: StudioClass[] };
  spec: { spec: StudioSpec };
  done: unknown;
}

export type StudioResult =
  | {
      kind: "table";
      columns: { name: string; label: string }[];
      rows: { source: string; values: Record<string, string | null> }[];
    }
  | {
      kind: "report";
      fields: FieldValue[];
      transcript: string;
      file: { name: string; url: string } | null;
    }
  | {
      kind: "answer";
      question: string;
      answer: string;
      sources: { title: string; page: number | null; quote: string }[];
      found: boolean;
    }
  | {
      kind: "labels";
      rows: {
        source: string;
        label: string;
        confidence: number;
        probabilities: Record<string, number>;
      }[];
    };

export interface StudioRunStepEvent {
  id: string;
  status: "running" | "done" | "error";
  ms: number | null;
  note: string | null;
}

/** SSE events of POST /api/studio/run (multipart: spec, files[] or sample_id, question?) */
export interface StudioRunEvents {
  step: StudioRunStepEvent;
  result: StudioResult;
  done: unknown;
}

/** POST /api/studio/export */
export interface StudioExportRequest {
  spec: StudioSpec;
}

export interface StudioExport {
  python: string;
  blueprint: Record<string, unknown>;
}

// ── Section 8.4: AI Act ──────────────────────────────────────────────────────

export type Tier = "prohibited" | "high" | "transparency" | "minimal";

export interface Citation {
  /** "annex-III-4-a", "art-50" */
  unit_id: string;
  /** The regulation's own citation style: "Liite III, 4 kohdan a alakohta" */
  label: string;
  /** Verbatim words from the regulation. */
  quote: string;
  /** The quote was found in the unit's text. */
  verified: boolean;
}

export interface Obligation {
  title: string;
  summary: string;
  who: "provider" | "deployer" | "both";
  citation: Citation;
}

export interface Assessment {
  id: string;
  description: string;
  tier: Tier;
  probabilities: Record<Tier, number>;
  confidence: number;
  /** Low confidence or the two methods disagree. */
  needs_human_review: boolean;
  /** Two or three Finnish sentences. */
  reasoning: string;
  basis: Citation[];
  obligations: Obligation[];
  language: "fi";
  disclaimer: string;
}

/** GET /api/ai-act/examples returns AiActExample[] */
export interface AiActExample {
  id: string;
  title: string;
  description: string;
}

/** POST /api/ai-act/assess */
export interface AssessRequest {
  description: string;
}

export interface AssessStepEvent {
  id: string;
  label: string;
  status: "running" | "done" | "error";
  ms: number | null;
  component: string | null;
}

/** SSE events of POST /api/ai-act/assess */
export interface AssessEvents {
  step: AssessStepEvent;
  partial: { probabilities: Record<Tier, number> };
  result: { assessment: Assessment };
  done: unknown;
}

/** GET /api/ai-act/units/{unit_id} */
export interface AiActUnit {
  id: string;
  kind: string;
  label: string;
  title: string;
  text: string;
  paragraphs: string[];
}

export interface AiActSearchHit {
  unit_id: string;
  label: string;
  snippet: string;
  score: number;
}

/** GET /api/ai-act/search?q= */
export interface AiActSearch {
  hits: AiActSearchHit[];
}

/** POST /api/ai-act/report (answers with a docx file) */
export interface AiActReportRequest {
  assessment: Assessment;
}

export interface AiActEvalCase {
  id: string;
  title: string;
  expected: Tier;
  got: Tier;
  ok: boolean;
}

/** GET /api/ai-act/eval */
export interface AiActEval {
  ran_at: string;
  total: number;
  correct: number;
  cases: AiActEvalCase[];
}

// ── Section 8.5: MCP ─────────────────────────────────────────────────────────

/** Tool names are the contract. */
export type McpToolName =
  | "parse_document"
  | "extract_fields"
  | "transcribe"
  | "classify"
  | "search_ai_act"
  | "assess_ai_system"
  | "list_reports"
  | "get_report"
  | "create_report";

export interface McpTool {
  name: string;
  description: string;
  /** The tool's arguments as the server describes them. */
  arguments: unknown;
}

export interface McpClient {
  id: string;
  title: string;
  snippet: string;
}

/** GET /api/mcp/info (never holds the key) */
export interface McpInfo {
  url: string;
  tools: McpTool[];
  clients: McpClient[];
}

/** GET /api/mcp/activity returns McpActivity[] */
export interface McpActivity {
  id: string;
  at: string;
  tool: string;
  client: string;
  ok: boolean;
  ms: number | null;
  summary: string;
}

/** GET /api/mcp/activity/stream: `call` per tool call, started and finished */
export interface McpActivityStreamEvents {
  call: McpActivity;
}

/** POST /api/mcp/try */
export interface McpTryRequest {
  tool: string;
  arguments: Record<string, unknown>;
}

// ── Section 9: WhatsApp bridge status (shown on the Soita page) ──────────────

/** GET /api/whatsapp/status */
export interface WhatsAppStatus {
  active: boolean;
  last_poll_at: string | null;
  queued: number;
}

// ── Section 10: "Näin tämä on tehty" ─────────────────────────────────────────

export interface HowStep {
  id: string;
  title: string;
  detail: string;
  /** gaik component behind the step, shown in mono. */
  gaik?: string;
  /** Logo id from `lib/stack.ts`. */
  logo?: string;
}

export interface How {
  title: string;
  steps: HowStep[];
  /** Logo ids from `lib/stack.ts` (files in public/stack). */
  stack: string[];
}
