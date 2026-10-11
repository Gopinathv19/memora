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

export const metadata = { title: "Chunks" };

const CHUNK_JSON = `[
  {
    "id": "9c2e…",
    "semantic_block_id": "e04f…",
    "extraction_id": "e27a…",
    "source_version": 1,
    "chunk_index": 7,
    "content": "Payment terms: net 30 days from the invoice date…",
    "embedding_text": "Terms\\n\\nPayment terms: net 30 days from the invoice date…",
    "token_count": 112,
    "content_type": "mixed",
    "page_start": 2,
    "page_end": 2,
    "section_path": ["Terms"],
    "is_active": true,
    "created_at": "2026-10-02T08:14:03.201Z"
  }
]`;

export default function ChunksPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Chunks">
        A chunk is a retrieval-sized piece of an extracted document. It is
        what gets embedded, searched, and returned as evidence for an answer.
      </DocHeader>

      <Section title="How chunks are made">
        <P>
          Chunking reads the page-by-page text that an{" "}
          <Link href="/docs/extractions" className="text-accent hover:underline">
            extraction
          </Link>{" "}
          stored, so it never reads the original file again. It runs locally
          and makes no model calls.
        </P>
        <Ul>
          <li>
            The text is parsed into elements: headings, paragraphs, lists,
            tables, key-value pairs and figures.
          </li>
          <li>
            Elements under the same heading are grouped into a section, which
            remembers its <C>section_path</C>, for example{" "}
            <C>[&quot;Policies&quot;, &quot;Leave&quot;]</C>.
          </li>
          <li>
            Sections are split into chunks of roughly 300–500 tokens. Large
            tables are split by groups of rows, and the header is repeated in
            each piece.
          </li>
          <li>
            Each chunk&apos;s <C>embedding_text</C> is its section path
            followed by its content. That&apos;s the text that gets embedded,
            so a short paragraph still carries the context of its heading.
          </li>
        </Ul>
        <Callout title="Automatic after extraction">
          When an extraction finishes, its source is chunked and the new chunks
          are queued for{" "}
          <Link href="/docs/embeddings" className="text-accent hover:underline">
            embedding
          </Link>
          . You only call rechunk to regenerate chunks, for example after the
          chunking rules change on the server, or to chunk an older extraction
          version.
        </Callout>
      </Section>

      <Section title="Rechunk a source">
        <Endpoint method="POST" path="/api/v1/sources/{source_id}/rechunk">
          Regenerates the source&apos;s chunks from an extraction&apos;s stored
          readings. It runs synchronously and costs nothing.
        </Endpoint>
        <Fields
          title="Query parameters"
          rows={[
            {
              name: "version",
              type: "integer",
              description: "The extraction version to chunk. Defaults to the latest.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `result = client.post(f"/sources/{source_id}/rechunk").json()
# {"extraction_id": "e27a…", "source_id": "4b19…", "version": 1, "chunk_count": 42}`,
            js: `const result = await memora(\`/sources/\${sourceId}/rechunk\`, { method: "POST" });`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/rechunk \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <Ul>
          <li>
            The new chunks become active and the previous ones are deactivated
            in the same transaction, so a search never sees a mix of the two.
          </li>
          <li>
            The new chunks are queued for embedding. A chunk whose text
            hasn&apos;t changed keeps the same input hash and isn&apos;t
            embedded again.
          </li>
          <li>
            <C>422</C>: the extraction has no stored readings to chunk. Re-run
            the extraction first.
          </li>
          <li>
            <C>404</C>: the source or extraction version is outside your scope
            or doesn&apos;t exist.
          </li>
        </Ul>
      </Section>

      <Section title="List a source's chunks">
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/chunks">
          The source&apos;s chunks in document order.
        </Endpoint>
        <Fields
          title="Query parameters"
          rows={[
            {
              name: "active_only",
              type: "boolean",
              description: (
                <>
                  Defaults to <C>true</C>. Set it to <C>false</C> to also return
                  chunks from older versions, which are kept for history.
                </>
              ),
            },
          ]}
        />
        <ResponseBlock json={CHUNK_JSON} />
        <P>
          <C>content_type</C> is <C>table</C>, <C>key_value</C> or{" "}
          <C>figure</C> when a chunk is entirely that kind of element, and{" "}
          <C>mixed</C> otherwise. <C>page_start</C> and <C>page_end</C> are{" "}
          <C>null</C> for formats without pages.
        </P>
      </Section>
    </>
  );
}
