import Link from "next/link";

import {
  C,
  Callout,
  DocHeader,
  Endpoint,
  Fields,
  P,
  Section,
  Ul,
} from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL } from "@/lib/config";

export const metadata = { title: "Extractions" };

const EXTRACTION_JSON = `{
  "id": "e27a…",
  "source_id": "4b19…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "version": 1,
  "status": "completed",
  "mode": "standard",
  "instructions": null,
  "provider": "nebius",
  "models": {
    "layout": "nvidia/nemotron-parse",
    "vision": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "extract": "nvidia/nemotron-3-super-120b-a12b"
  },
  "error": null,
  "prompt_tokens": 6120,
  "completion_tokens": 904,
  "cost_usd": 0.002649,
  "triggered_by_kind": "credential",
  "triggered_by_user_id": null,
  "triggered_by_credential_id": "b0c3…",
  "actor_id": "c8d2…",
  "created_at": "2026-09-24T10:02:11.120Z",
  "finished_at": "2026-09-24T10:02:29.874Z",
  "result": {
    "source_id": "4b19…",
    "document_type": "invoice",
    "title": "Invoice INV-2041",
    "language": "en",
    "summary": "Invoice from Acme Ltd to Globex for 4 widgets, 12,400 INR due 2026-10-01.",
    "fields": [
      { "key": "invoice_number", "value": "INV-2041", "page": 1, "confidence": 0.98 },
      { "key": "total_amount", "value": "12,400 INR", "page": 2, "confidence": 0.93 }
    ],
    "tables": [
      {
        "title": "Line items",
        "page": 2,
        "columns": ["Item", "Qty", "Price"],
        "rows": [["Widget", "4", "3,100"]]
      }
    ],
    "pages": [
      { "page": 1, "kind": "page", "difficulty": "easy", "route": "text",
        "model": null, "status": "ok", "note": null },
      { "page": 2, "kind": "page", "difficulty": "hard", "route": "layout",
        "model": "nvidia/nemotron-parse", "status": "ok", "note": null }
    ],
    "status": "completed",
    "warnings": []
  },
  "usage": [
    { "id": "…", "provider": "nebius", "model": "nvidia/nemotron-parse",
      "role": "layout", "page": 2, "prompt_tokens": 1830, "completion_tokens": 412,
      "cost_usd": 0.0, "price": null, "latency_ms": 6210, "status": "ok", "error": null,
      "created_at": "…" },
    { "id": "…", "provider": "nebius", "model": "nvidia/nemotron-3-super-120b-a12b",
      "role": "extract", "page": null, "prompt_tokens": 4290, "completion_tokens": 492,
      "cost_usd": 0.002649,
      "price": { "input_per_1m": 0.30, "output_per_1m": 0.90, "per_image": 0,
                 "per_call": 0, "free": false, "effective_from": "2026-09-01" },
      "latency_ms": 11380, "status": "ok", "error": null,
      "created_at": "…" }
  ]
}`;

export default function ExtractionsPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Extractions">
        The Extraction Agent reads a stored file with NVIDIA models and returns
        structured information: a summary, key fields, and tables, each value
        traced to the page it came from. Every run is kept as a numbered
        version.
      </DocHeader>

      <Callout title="Extraction only runs when asked">
        Uploading a file never spends model credits on its own. Ask for
        extraction with <C>extract=true</C> on the upload, or start it later with{" "}
        <C>POST /sources/{"{source_id}"}/extractions</C>. Runs happen in the
        background: the request returns at once and you poll for the result.
      </Callout>

      <Section title="How a document is read">
        <P>
          Each page (or slide, sheet or image) is triaged locally first, at no
          cost, and only the parts local parsing cannot read reliably go to a
          model:
        </P>
        <Ul>
          <li>
            <strong className="font-medium text-ink">Easy</strong> — clean
            digital text, no tables, few pictures. Read locally; route{" "}
            <C>text</C>, no model.
          </li>
          <li>
            <strong className="font-medium text-ink">Medium</strong> — good text
            plus one or two pictures. Text read locally, pictures described by the
            vision model; route <C>vision</C>.
          </li>
          <li>
            <strong className="font-medium text-ink">Hard</strong> — a table, a
            scan, garbled text, or a picture-heavy page. The whole page is
            rendered and transcribed by the layout model; route <C>layout</C>.
          </li>
        </Ul>
        <P>
          Everything is then merged in reading order and sent once to the
          extraction model, which returns the result below. Supported files:
          PDF, PNG/JPEG/WebP and other images, DOCX, PPTX, XLSX, and text (TXT,
          Markdown, CSV, JSON). Legacy <C>.doc</C>/<C>.ppt</C>/<C>.xls</C> and
          HEIC images are rejected with a clear error.
        </P>
        <Fields
          title="Model roles"
          rows={[
            {
              name: "layout",
              type: "page image → Markdown",
              description: (
                <>
                  <C>nvidia/nemotron-parse</C> on build.nvidia.com. On Nebius,
                  configure an NVIDIA vision model here instead.
                </>
              ),
            },
            {
              name: "vision",
              type: "picture → description",
              description: <C>nvidia/nemotron-3-nano-omni-30b-a3b-reasoning</C>,
            },
            {
              name: "extract",
              type: "document → JSON",
              description: <C>nvidia/nemotron-3-super-120b-a12b</C>,
            },
          ]}
        />
        <P>
          Models are server configuration, not request parameters, and must be
          NVIDIA models. Which ones ran is recorded on every version in{" "}
          <C>models</C>.
        </P>
      </Section>

      <Section title="Extract on upload">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources/upload">
          The ordinary{" "}
          <Link href="/docs/sources" className="text-accent hover:underline">
            upload
          </Link>
          , with three optional form fields. The response is the source, already
          in <C>processing</C>; version 1 runs in the background.
        </Endpoint>
        <Fields
          title="Extra form fields"
          rows={[
            {
              name: "extract",
              type: "boolean",
              description: (
                <>
                  <C>true</C> to start extraction as soon as the file is stored.
                  Defaults to <C>false</C>: the source stays <C>pending</C>.
                </>
              ),
            },
            {
              name: "extract_mode",
              type: "string",
              description: (
                <>
                  <C>standard</C> (default) or <C>deep</C>. See below.
                </>
              ),
            },
            {
              name: "extract_instructions",
              type: "string | null",
              description: "Optional guidance for the agent, up to 2,000 characters.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `with open("invoice.pdf", "rb") as handle:
    source = client.post(
        f"/subjects/{subject_id}/sources/upload",
        files={"file": ("invoice.pdf", handle, "application/pdf")},
        data={"extract": "true", "created_by_actor_id": actor_id},
    ).json()
# source["status"] == "processing"`,
            js: `const form = new FormData();
form.append("file", file, "invoice.pdf");
form.append("extract", "true");

const source = await memora(\`/subjects/\${subjectId}/sources/upload\`, {
  method: "POST",
  body: form,
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/subjects/$SUBJECT_ID/sources/upload \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -F "file=@invoice.pdf;type=application/pdf" \\
  -F "extract=true"`,
          }}
        />
      </Section>

      <Section title="Start, retry or re-extract">
        <Endpoint method="POST" path="/api/v1/sources/{source_id}/extractions">
          Starts the next version. The same call covers a first extraction of a
          file uploaded without <C>extract</C>, a retry after <C>failed</C>, and
          a re-run when the result missed something. Returns <C>202</C> with the
          new version in <C>processing</C>. The body is optional.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "mode",
              type: "string",
              description: (
                <>
                  <C>standard</C> triages each page (cheapest). <C>deep</C> sends
                  every PDF page to the layout model — more accurate on awkward
                  documents, more credits. Other formats read the same in both.
                </>
              ),
            },
            {
              name: "instructions",
              type: "string | null",
              description: (
                <>
                  What to look for, e.g. &ldquo;capture the premium table on page 3
                  and the policy number&rdquo;. Up to 2,000 characters. It steers
                  what is captured; it cannot change the response format.
                </>
              ),
            },
            {
              name: "actor_id",
              type: "uuid | null",
              description:
                "Your end user this run is for. Recorded for cost attribution only; must belong to the same application.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `run = client.post(
    f"/sources/{source_id}/extractions",
    json={"mode": "deep", "instructions": "capture the premium table on page 3"},
).json()
# run["version"] == 2, run["status"] == "processing"`,
            js: `const run = await memora(\`/sources/\${sourceId}/extractions\`, {
  method: "POST",
  body: JSON.stringify({
    mode: "deep",
    instructions: "capture the premium table on page 3",
  }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/extractions \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"mode":"deep","instructions":"capture the premium table on page 3"}'`,
          }}
        />
        <Ul>
          <li>
            <C>409</C> — a run for this source is already processing. Wait for it.
          </li>
          <li>
            <C>422</C> — the source has no stored bytes (it was registered by
            reference), or <C>actor_id</C> is from another application.
          </li>
          <li>
            <C>404</C> — the source is outside your credential&apos;s scope.
          </li>
        </Ul>
      </Section>

      <Section title="Read the result">
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/extractions/latest">
          The newest version, with its result and its model calls. Poll this
          every few seconds after starting a run.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/extractions/{version}">
          A specific version. Old versions are never overwritten.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/extractions">
          Every version, newest first, without <C>result</C> and <C>usage</C>.
        </Endpoint>
        <P>A version&apos;s status is one of:</P>
        <Ul>
          <li>
            <C>processing</C> — the agent is working.
          </li>
          <li>
            <C>completed</C> — every page was read.
          </li>
          <li>
            <C>partial</C> — a result exists, but some pages could not be read or
            were beyond the page limit. See <C>result.warnings</C>.
          </li>
          <li>
            <C>failed</C> — nothing usable came out; <C>error</C> says why.
            Start a new version to retry.
          </li>
        </Ul>
        <P>
          The source&apos;s own status follows its latest run: <C>processing</C>{" "}
          while it runs, then <C>completed</C> or <C>failed</C>.
        </P>
        <CodeTabs
          sample={{
            python: `import time

while True:
    run = client.get(f"/sources/{source_id}/extractions/latest").json()
    if run["status"] != "processing":
        break
    time.sleep(3)

for field in run["result"]["fields"]:
    print(field["key"], "=", field["value"], "(page", field["page"], ")")`,
            js: `let run;
do {
  await new Promise((r) => setTimeout(r, 3000));
  run = await memora(\`/sources/\${sourceId}/extractions/latest\`);
} while (run.status === "processing");`,
            curl: `curl -s ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/extractions/latest \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <ResponseBlock json={EXTRACTION_JSON} />
      </Section>

      <Section title="The result">
        <P>
          One generic shape for every kind of document. The agent chooses the
          field keys — <C>invoice_number</C>, <C>date_of_birth</C>,{" "}
          <C>policy_number</C> — so there is no per-document schema to maintain.
        </P>
        <Fields
          title="result"
          rows={[
            { name: "document_type", type: "string", description: "snake_case kind, e.g. invoice, aadhaar_card, resume; unknown if unclear." },
            { name: "title", type: "string | null", description: "Short title of the document." },
            { name: "language", type: "string | null", description: "ISO 639-1 code of the main language." },
            { name: "summary", type: "string", description: "A factual 2–4 sentence summary." },
            { name: "fields[]", type: "object", description: <>Each fact: <C>key</C>, <C>value</C> (string, as written), <C>page</C>, <C>confidence</C> (0–1).</> },
            { name: "tables[]", type: "object", description: <>Each table: <C>title</C>, <C>page</C>, <C>columns</C>, <C>rows</C>.</> },
            { name: "pages[]", type: "object", description: <>How each unit was read: <C>difficulty</C>, <C>route</C>, <C>model</C>, <C>status</C> (ok, fallback, failed), <C>note</C>.</> },
            { name: "status", type: "string", description: <><C>completed</C> or <C>partial</C>.</> },
            { name: "warnings[]", type: "string", description: "Pages skipped, pictures not described, text truncated." },
          ]}
        />
        <Callout>
          Extraction returns facts only. It does not create chunks, embeddings or
          graph entities — the original file stays the source of truth and can
          always be re-read.
        </Callout>
      </Section>

      <Section title="Usage and cost">
        <Endpoint method="GET" path="/api/v1/usage/extractions">
          Totals of every model call inside your scope, grouped by application,
          by model and by who triggered them. Filter with <C>?tenant_id=</C>,{" "}
          <C>?application_id=</C>, <C>?from=</C> and <C>?to=</C> (ISO-8601).
        </Endpoint>
        <P>
          Each call is also on its version, under <C>usage</C>, and the
          version carries the totals <C>prompt_tokens</C>,{" "}
          <C>completion_tokens</C> and <C>cost_usd</C>. Failed calls are
          recorded too. Costs are what the model provider charges Memora,
          worked out from the operator&apos;s price list: per model, by input and
          output tokens, per image and/or per call. Calls through
          build.nvidia.com are free.
        </P>
        <P>
          Prices are set by whoever runs Memora, not through this API: no
          endpoint can read or change them. Each model call records the rate
          that was applied in <C>price</C>, with the date that rate took effect,
          so a later price change never alters a past cost. <C>price</C> is{" "}
          <C>null</C> when a model has no price; that call is recorded at $0.
        </P>
        <CodeTabs
          sample={{
            python: `report = client.get("/usage/extractions").json()
print(report["totals"]["cost_usd"])`,
            js: `const report = await memora("/usage/extractions");`,
            curl: `curl -s "${API_BASE_URL}/api/v1/usage/extractions?from=2026-09-01T00:00:00Z" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <ResponseBlock
          json={`{
  "totals": { "runs": 12, "calls": 31, "prompt_tokens": 84210,
              "completion_tokens": 9120, "cost_usd": 0.041 },
  "by_application": [
    { "key": "6f1c…", "label": "ThinkFill", "runs": 12, "calls": 31, "…": "…" }
  ],
  "by_model": [
    { "key": "nvidia/nemotron-3-super-120b-a12b", "label": "extract",
      "runs": 12, "calls": 12, "…": "…" }
  ],
  "by_trigger": [
    { "key": "credential:b0c3…", "label": "Production", "runs": 10, "…": "…" }
  ]
}`}
        />
      </Section>
    </>
  );
}
