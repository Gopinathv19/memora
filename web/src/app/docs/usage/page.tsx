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

export const metadata = { title: "Usage and stats" };

const USAGE_JSON = `{
  "totals": {
    "runs": 18,
    "calls": 211,
    "prompt_tokens": 402118,
    "completion_tokens": 61204,
    "cost_usd": 0.184512
  },
  "by_application": [
    { "key": "6f1c…", "label": "ThinkFill", "runs": 18, "calls": 211, "prompt_tokens": 402118, "completion_tokens": 61204, "cost_usd": 0.184512 }
  ],
  "by_model": [
    { "key": "nvidia/nemotron-3-super-120b-a12b", "label": "extract", "runs": 18, "calls": 18, "prompt_tokens": 110230, "completion_tokens": 16012, "cost_usd": 0.051220 },
    { "key": "nvidia/nemotron-3-super-120b-a12b", "label": "graph", "runs": 6, "calls": 154, "prompt_tokens": 251988, "completion_tokens": 40112, "cost_usd": 0.118034 },
    { "key": "nvidia/nemotron-parse", "label": "layout", "runs": 4, "calls": 39, "prompt_tokens": 39900, "completion_tokens": 5080, "cost_usd": 0.015258 }
  ],
  "by_trigger": [
    { "key": "credential:b0c3…", "label": "Production", "runs": 18, "calls": 211, "prompt_tokens": 402118, "completion_tokens": 61204, "cost_usd": 0.184512 }
  ]
}`;

const STATS_JSON = `{
  "tenants": 1,
  "applications": 1,
  "actors": 12,
  "subjects": 37,
  "sources": 214,
  "active_credentials": 2,
  "sources_by_status": {
    "pending": 3,
    "processing": 1,
    "completed": 207,
    "failed": 3
  }
}`;

export default function UsagePage() {
  return (
    <>
      <DocHeader eyebrow="Reference" title="Usage and stats">
        What your model calls cost and who triggered them, plus live counts of
        everything in your scope.
      </DocHeader>

      <Section title="Model usage and cost">
        <Endpoint method="GET" path="/api/v1/usage/extractions">
          Totals for every model call made by{" "}
          <Link href="/docs/extractions" className="text-accent hover:underline">
            extractions
          </Link>{" "}
          and{" "}
          <Link href="/docs/graph" className="text-accent hover:underline">
            graph builds
          </Link>{" "}
          in your scope, grouped three ways.
        </Endpoint>
        <Fields
          title="Query parameters"
          rows={[
            {
              name: "tenant_id",
              type: "uuid",
              description: "Narrow to one tenant. Useful for a console user who owns several.",
            },
            { name: "application_id", type: "uuid", description: "Narrow to one application." },
            { name: "from", type: "datetime", description: "Include calls made at or after this time." },
            { name: "to", type: "datetime", description: "Include calls made before this time." },
          ]}
        />
        <CodeTabs
          sample={{
            python: `report = client.get(
    "/usage/extractions",
    params={"from": "2026-10-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"},
).json()
print(report["totals"]["cost_usd"])`,
            js: `const report = await memora(
  "/usage/extractions?from=2026-10-01T00:00:00Z&to=2026-11-01T00:00:00Z",
);`,
            curl: `curl -s "${API_BASE_URL}/api/v1/usage/extractions?from=2026-10-01T00:00:00Z" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <ResponseBlock json={USAGE_JSON} />
        <Ul>
          <li>
            <C>runs</C> is the number of distinct extraction runs, and{" "}
            <C>calls</C> is the number of individual model calls.
          </li>
          <li>
            <C>by_application</C>: <C>key</C> is the application id and{" "}
            <C>label</C> its name.
          </li>
          <li>
            <C>by_model</C>: <C>key</C> is the model and <C>label</C> its role:{" "}
            <C>layout</C>, <C>vision</C>, <C>extract</C> or <C>graph</C>.
          </li>
          <li>
            <C>by_trigger</C>: who started the work. For a credential,{" "}
            <C>label</C> is its name. For a console user, it is their email.
          </li>
        </Ul>
        <Callout title="Not included">
          Calls to the embedding model and to the answer-writing model behind{" "}
          <C>POST /query</C> are not in this report. Each query returns its own
          tokens and latency in <C>usage</C>, and embedding has no per-call
          cost record yet.
        </Callout>
      </Section>

      <Section title="Live counts">
        <Endpoint method="GET" path="/api/v1/stats">
          How many tenants, applications, actors, subjects, sources and active
          credentials your caller can see, with sources broken down by status.
        </Endpoint>
        <ResponseBlock json={STATS_JSON} />

        <Endpoint method="GET" path="/api/v1/stats/metrics">
          Daily activity over a window. The buckets are computed in the
          database, so the response is the same size however much data you
          have.
        </Endpoint>
        <Fields
          title="Query parameters"
          rows={[
            { name: "days", type: "integer", description: "Size of the window, 7 to 90. Defaults to 30." },
          ]}
        />
        <P>
          <C>sources</C>, <C>subjects</C> and <C>actors</C> are each a daily
          series of <C>points</C> (<C>date</C>, <C>value</C>), with days that
          had no activity included as zero. Each series also has{" "}
          <C>current</C>, the total for this window, and <C>previous</C>, the
          total for the window of the same length before it. The response also
          includes <C>sources_by_type</C>, <C>storage_bytes</C>,{" "}
          <C>largest_source_bytes</C> and <C>last_source_at</C>.
        </P>
      </Section>
    </>
  );
}
