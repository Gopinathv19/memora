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

export const metadata = { title: "Knowledge graph" };

const BUILD_JSON = `{
  "id": "9d41…",
  "source_id": "4b19…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "extraction_id": "e27a…",
  "extraction_version": 1,
  "retry_of_id": null,
  "status": "completed",
  "provider": "nebius",
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "chunk_chars": 3000,
  "chunk_overlap": 300,
  "chunk_count": 4,
  "failed_chunk_count": 0,
  "entity_count": 17,
  "relationship_count": 12,
  "failed_chunks": [],
  "stats": { "entities_created": 11, "entities_matched": 6, "relationships_skipped": 1 },
  "error": null,
  "prompt_tokens": 9120,
  "completion_tokens": 2210,
  "cost_usd": 0.004725,
  "triggered_by_kind": "credential",
  "created_at": "2026-09-25T10:04:02.011Z",
  "finished_at": "2026-09-25T10:04:19.530Z",
  "usage": [
    { "role": "graph", "model": "nvidia/nemotron-3-super-120b-a12b", "page": 1,
      "prompt_tokens": 2280, "completion_tokens": 551, "cost_usd": 0.00118,
      "status": "ok", "…": "…" }
  ]
}`;

const QUERY_JSON = `{
  "seed_entity_ids": ["0c7e…"],
  "entities": [
    { "entity_id": "0c7e…", "name": "Dell", "entity_type": "COMPANY",
      "description": "Supplier of the data-centre servers", "aliases": [],
      "mention_count": 2, "source_ids": ["4b19…"], "source_chunk_ids": ["4b19…:1:0"] },
    { "entity_id": "5a2d…", "name": "Microsoft Corporation", "entity_type": "COMPANY",
      "description": null, "aliases": ["MSFT"], "mention_count": 3,
      "source_ids": ["4b19…"], "source_chunk_ids": ["4b19…:1:0", "4b19…:1:2"] },
    { "entity_id": "f19b…", "name": "Azure", "entity_type": "TECHNOLOGY",
      "description": null, "aliases": [], "mention_count": 1,
      "source_ids": ["4b19…"], "source_chunk_ids": ["4b19…:1:2"] }
  ],
  "relationships": [
    { "source_entity_id": "0c7e…", "source_name": "Dell", "relation": "PROVIDES_TO",
      "target_entity_id": "5a2d…", "target_name": "Microsoft Corporation",
      "description": "Dell provides servers to Microsoft", "confidence": 0.9,
      "raw_relation": null, "source_ids": ["4b19…"], "source_chunk_ids": ["4b19…:1:0"] },
    { "source_entity_id": "5a2d…", "source_name": "Microsoft Corporation", "relation": "USES",
      "target_entity_id": "f19b…", "target_name": "Azure",
      "description": null, "confidence": 0.85, "raw_relation": null,
      "source_ids": ["4b19…"], "source_chunk_ids": ["4b19…:1:2"] }
  ],
  "source_chunk_ids": ["4b19…:1:0", "4b19…:1:2"],
  "chunks": [
    { "chunk_id": "4b19…:1:0", "source_id": "4b19…", "index": 0,
      "page_start": 1, "page_end": 2, "char_start": 34, "char_end": 2987,
      "text": "Dell provides servers to Microsoft Corporation under …" }
  ]
}`;

export default function GraphPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Knowledge graph">
        Memora reads each extracted document for the people, organizations,
        products, places and documents it names, and the relationships it
        states between them. The results form one knowledge graph per tenant.
        Every fact keeps the source and passage it came from, and a
        subject&apos;s graph only ever shows facts from that subject&apos;s own
        sources.
      </DocHeader>

      <Callout title="The graph is built only when asked">
        A graph build makes one model call per passage of the document, so it
        never starts on its own. Ask for it with <C>build_graph=true</C> when
        you upload or extract, or with <C>POST /sources/{"{source_id}"}/graph</C>{" "}
        once an extraction has completed. Builds run in the background.
      </Callout>

      <Section title="How a graph is built">
        <Ul>
          <li>
            The text the{" "}
            <Link href="/docs/extractions" className="text-accent hover:underline">
              extraction
            </Link>{" "}
            read is split into passages of about 3,000 characters. A passage
            follows page and paragraph boundaries, and a table is never cut in
            half if it fits.
          </li>
          <li>
            An NVIDIA model reads each passage and returns entities and
            relationships. Only types from Memora&apos;s fixed list are used, for
            example <C>PERSON</C>, <C>COMPANY</C> or <C>LOCATION</C>, and{" "}
            <C>WORKS_FOR</C>, <C>PROVIDES_TO</C> or <C>LOCATED_IN</C>. A label
            the list doesn&apos;t know is stored as <C>RELATED_TO</C>, and the
            model&apos;s own wording is kept in <C>raw_relation</C>.
          </li>
          <li>
            Spellings of the same thing are merged into one entity. For
            example, &ldquo;Microsoft&rdquo;, &ldquo;Microsoft Corp.&rdquo; and
            &ldquo;Microsoft Corporation&rdquo; become one entity, and so do
            &ldquo;IBM&rdquo; and &ldquo;International Business Machines&rdquo;.
          </li>
          <li>
            A build uses the source&apos;s latest completed extraction and
            replaces whatever that source contributed before. An entity that
            other sources also mention stays.
          </li>
        </Ul>
      </Section>

      <Section title="Build a source's graph">
        <Endpoint method="POST" path="/api/v1/sources/{source_id}/graph">
          Starts a build from the source&apos;s latest completed extraction and
          returns <C>202</C> with the build in <C>processing</C>. The body is
          optional.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "retry_failed",
              type: "boolean",
              description: (
                <>
                  <C>true</C> to re-read only the passages the latest build could
                  not, from the same extraction version. Needs a latest build
                  that is <C>partial</C>. Defaults to <C>false</C>, which starts
                  a full build.
                </>
              ),
            },
            {
              name: "actor_id",
              type: "uuid | null",
              description:
                "Your end user this build is for. It is recorded for cost attribution only, and must belong to the same application.",
            },
          ]}
        />
        <P>
          To extract and build in one go, add <C>build_graph=true</C> to the
          upload form alongside <C>extract=true</C>, or add{" "}
          <C>&quot;build_graph&quot;: true</C> to{" "}
          <C>POST /sources/{"{source_id}"}/extractions</C>. The build starts once
          the extraction succeeds.
        </P>
        <CodeTabs
          sample={{
            python: `build = client.post(f"/sources/{source_id}/graph", json={}).json()
# build["status"] == "processing"`,
            js: `const build = await memora(\`/sources/\${sourceId}/graph\`, {
  method: "POST",
  body: JSON.stringify({}),
});`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/graph \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <Ul>
          <li>
            <C>409</C>: a build is already running for this source.
          </li>
          <li>
            <C>422</C>: returned in any of these cases:
            <Ul>
              <li>the source has no completed extraction yet;</li>
              <li>
                its extraction is older than the knowledge graph, so re-extract
                it first;
              </li>
              <li>
                <C>retry_failed</C> was sent but the latest build isn&apos;t{" "}
                <C>partial</C>.
              </li>
            </Ul>
          </li>
          <li>
            <C>503</C>: the knowledge graph isn&apos;t enabled on this Memora
            server.
          </li>
          <li>
            <C>404</C>: the source is outside your credential&apos;s scope.
          </li>
        </Ul>
      </Section>

      <Section title="Build status">
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/graph/builds/latest">
          The newest build, with every model call it made. Poll this after
          starting a build.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/graph/builds">
          Every build, newest first, without <C>usage</C>.
        </Endpoint>
        <P>A build&apos;s status is one of:</P>
        <Ul>
          <li>
            <C>processing</C>: the build is running.
          </li>
          <li>
            <C>completed</C>: every passage was read.
          </li>
          <li>
            <C>partial</C>: the graph was written, but some passages failed.
            They&apos;re listed in <C>failed_chunks</C>; retry them with{" "}
            <C>retry_failed</C>.
          </li>
          <li>
            <C>failed</C>: nothing was written, and <C>error</C> says why. The
            source keeps the graph it had before.
          </li>
        </Ul>
        <P>
          Graph model calls are included in{" "}
          <C>GET /usage/extractions</C> with role <C>graph</C>, priced like
          every other call.
        </P>
        <ResponseBlock json={BUILD_JSON} />
      </Section>

      <Section title="A subject's graph">
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}/graph">
          The subject&apos;s most-mentioned entities and the relationships
          between them, ready to plot. By default it includes every folder
          below the subject.
        </Endpoint>
        <Fields
          title="Query parameters"
          rows={[
            {
              name: "include_subfolders",
              type: "boolean",
              description: (
                <>
                  Defaults to <C>true</C>. Set it to <C>false</C> to include only
                  the sources directly in this subject.
                </>
              ),
            },
            { name: "max_entities", type: "integer", description: "1 to 1000. Defaults to 200." },
            { name: "max_relationships", type: "integer", description: "1 to 2000. Defaults to 500." },
          ]}
        />
      </Section>

      <Section title="Ask the graph">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/graph/query">
          Graph evidence for a question: the entities it names, what they
          connect to within <C>max_hops</C>, and the passages behind every
          fact. It is evidence to give your own model, not an answer.
        </Endpoint>
        <Fields
          rows={[
            { name: "query", type: "string", description: "The question. Up to 1,000 characters." },
            { name: "max_hops", type: "integer", description: "1 to 3. Defaults to 2." },
            { name: "max_entities", type: "integer", description: "1 to 100. Defaults to 20." },
            { name: "max_relationships", type: "integer", description: "1 to 200. Defaults to 50." },
            { name: "include_subfolders", type: "boolean", description: <>Defaults to <C>true</C>.</> },
          ]}
        />
        <CodeTabs
          sample={{
            python: `evidence = client.post(
    f"/subjects/{subject_id}/graph/query",
    json={"query": "What cloud does Dell's customer use?", "max_hops": 2},
).json()
for fact in evidence["relationships"]:
    print(fact["source_name"], fact["relation"], fact["target_name"])`,
            js: `const evidence = await memora(\`/subjects/\${subjectId}/graph/query\`, {
  method: "POST",
  body: JSON.stringify({ query: "What cloud does Dell's customer use?", max_hops: 2 }),
});`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/subjects/$SUBJECT_ID/graph/query \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"What cloud does Dell'"'"'s customer use?","max_hops":2}'`,
          }}
        />
        <ResponseBlock json={QUERY_JSON} />
        <Callout>
          Retrieval is deterministic. Entities are matched by name, aliases
          and full-text search, then expanded through their relationships. No
          model runs at query time, so asking the graph costs no credits.
        </Callout>
      </Section>
    </>
  );
}
