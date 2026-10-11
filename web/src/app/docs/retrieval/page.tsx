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

export const metadata = { title: "Retrieval" };

const QUERY_JSON = `{
  "query": "What is the payment term on the Globex invoice?",
  "answer": "Payment is due within 30 days of the invoice date, by 2026-10-01 (Terms, page 2).",
  "strategy_id": "1d7e…",
  "strategy_name": "default",
  "chunks": [
    {
      "chunk_id": "9c2e…",
      "source_id": "4b19…",
      "source_filename": "INV-2041.pdf",
      "subject_id": "7a10…",
      "content": "Payment terms: net 30 days from the invoice date…",
      "content_type": "mixed",
      "section_path": ["Terms"],
      "page_start": 2,
      "page_end": 2,
      "token_count": 112,
      "similarity": 0.8312,
      "rerank_score": 0.9741
    }
  ],
  "retrieval": {
    "hnsw_candidates": 50,
    "mmr_candidates": 10,
    "final_chunks": 5,
    "reranker_used": true
  },
  "usage": {
    "prompt_tokens": 2140,
    "completion_tokens": 38,
    "latency_ms": 1820
  }
}`;

export default function RetrievalPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Retrieval">
        Ask a question and get an answer written only from your own embedded
        chunks, together with the chunks behind it. One synchronous call, with
        nothing to poll.
      </DocHeader>

      <Section title="How a query is answered">
        <P>
          Every query runs the same five steps. Each step is reported in the
          response, so you can see why a chunk was or wasn&apos;t used.
        </P>
        <Ul>
          <li>
            <strong className="font-medium text-ink">Embed the query</strong>{" "}
            with the same strategy, model and normalization as the stored
            vectors.
          </li>
          <li>
            <strong className="font-medium text-ink">HNSW search</strong> for
            the 50 nearest chunks by cosine similarity. Your tenant and
            application filters are applied inside this search, so chunks you
            don&apos;t own are never considered.
          </li>
          <li>
            <strong className="font-medium text-ink">MMR</strong> keeps the 10
            candidates that are relevant without repeating each other, so five
            copies of the same paragraph can&apos;t fill the context.
          </li>
          <li>
            <strong className="font-medium text-ink">Rerank</strong> those 10
            with a cross-encoder and keep the top 5, or <C>top_k</C>. If the
            reranker is unavailable, the MMR order is used and{" "}
            <C>reranker_used</C> is <C>false</C>. The query doesn&apos;t fail.
          </li>
          <li>
            <strong className="font-medium text-ink">Generate</strong> an answer
            from those chunks only. The model is told to treat the chunks as
            evidence rather than instructions, to say so when they don&apos;t
            contain the answer, and to name the document or section where it
            can. The chunks it was given are returned in <C>chunks</C>, so you
            can show them as citations.
          </li>
        </Ul>
        <Callout title="Only embedded chunks can be found">
          A source becomes searchable once it has been extracted, chunked and
          embedded. Uploading with <C>extract=true</C> does all three
          automatically. Check progress with{" "}
          <Link href="/docs/embeddings" className="text-accent hover:underline">
            <C>GET /embedding/sources/{"{source_id}"}/status</C>
          </Link>
          .
        </Callout>
      </Section>

      <Section title="Ask a question">
        <Endpoint method="POST" path="/api/v1/query">
          Runs the whole pipeline and returns the answer and its evidence. It
          writes nothing.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "query",
              type: "string",
              required: true,
              description: "The question. Up to 4,000 characters; must not be blank.",
            },
            {
              name: "subject_id",
              type: "uuid | null",
              description:
                "Search only the sources in this subject. Leave it out to search everything your credential can see.",
            },
            {
              name: "source_id",
              type: "uuid | null",
              description: "Search only this one source.",
            },
            {
              name: "strategy_id",
              type: "uuid | null",
              description: (
                <>
                  Which embedding strategy to search. Defaults to the active
                  one. See{" "}
                  <Link href="/docs/embeddings" className="text-accent hover:underline">
                    Embeddings
                  </Link>
                  .
                </>
              ),
            },
            {
              name: "generate",
              type: "boolean",
              description: (
                <>
                  Defaults to <C>true</C>. Set it to <C>false</C> to skip the
                  answer and get only the ranked chunks, for example to write
                  the answer with your own model or prompt. <C>answer</C> is
                  then an empty string and <C>usage</C> is zero.
                </>
              ),
            },
            {
              name: "top_k",
              type: "integer | null",
              description: "How many chunks to keep after reranking, 1 to 20. Defaults to 5.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `result = client.post(
    "/query",
    json={
        "query": "What is the payment term on the Globex invoice?",
        "subject_id": subject["id"],
    },
).json()

print(result["answer"])
for chunk in result["chunks"]:
    print(chunk["source_filename"], "p.", chunk["page_start"])`,
            js: `const result = await memora("/query", {
  method: "POST",
  body: JSON.stringify({
    query: "What is the payment term on the Globex invoice?",
    subject_id: subject.id,
  }),
});

console.log(result.answer);
for (const chunk of result.chunks) {
  console.log(chunk.source_filename, "p.", chunk.page_start);
}`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/query \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"What is the payment term on the Globex invoice?","subject_id":"'$SUBJECT_ID'"}'`,
          }}
        />
        <ResponseBlock json={QUERY_JSON} />
      </Section>

      <Section title="Reading the response">
        <Ul>
          <li>
            <C>chunks</C>: the final context, best first. <C>similarity</C> is
            the cosine similarity from the HNSW search and{" "}
            <C>rerank_score</C> is the reranker&apos;s score, which is{" "}
            <C>null</C> when the reranker wasn&apos;t used.{" "}
            <C>section_path</C>, <C>page_start</C> and <C>page_end</C> say
            where in the document the chunk came from.
          </li>
          <li>
            <C>retrieval</C>: how many candidates each step produced. If{" "}
            <C>hnsw_candidates</C> is <C>0</C>, nothing in scope is embedded
            yet. The answer then says so plainly and <C>chunks</C> is empty, so
            no answer is made up.
          </li>
          <li>
            <C>usage</C>: tokens and latency of the answer-writing call.
          </li>
        </Ul>
      </Section>

      <Section title="Errors">
        <Ul>
          <li>
            <C>422</C>: the query is blank or longer than 4,000 characters, or{" "}
            <C>top_k</C> is out of range.
          </li>
          <li>
            <C>404</C>: <C>subject_id</C>, <C>source_id</C> or{" "}
            <C>strategy_id</C> doesn&apos;t exist or is outside your
            credential&apos;s scope, or no embedding strategy is active.
          </li>
          <li>
            <C>503</C>: the embedding model or the answer model couldn&apos;t
            be reached. Retry later. A reranker outage doesn&apos;t cause this
            error.
          </li>
        </Ul>
      </Section>

      <Section title="Retrieval or graph?">
        <P>
          <C>POST /query</C> finds passages that are similar to the question.{" "}
          <Link href="/docs/graph" className="text-accent hover:underline">
            <C>POST /subjects/{"{subject_id}"}/graph/query</C>
          </Link>{" "}
          follows named entities and their relationships across documents.
          Use the first for &ldquo;what does the document say about X&rdquo;
          and the second for &ldquo;how is X connected to Y&rdquo;. The graph
          returns evidence only and runs no model.
        </P>
      </Section>
    </>
  );
}
