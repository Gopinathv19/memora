"use client";

import { api } from "@/lib/api";
import type { UsageReport } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { formatUsd } from "@/components/ExtractionPanel";
import { KeyValueGrid, LoadingState, ErrorState, Mono, Panel, TypeTag } from "@/components/ui";

/** Extraction usage and cost for an application (or everything in scope). */
export function UsageCard({ applicationId }: { applicationId?: string }) {
  const report = useResource<UsageReport>(
    () => api.usage.extractions({ applicationId }),
    [applicationId],
  );

  return (
    <Panel
      title="Extraction usage & cost"
      description="Every model call the Extraction Agent made, and who triggered it. build.nvidia.com calls are free; Nebius calls are priced from LLM_PRICES."
    >
      {report.loading ? (
        <LoadingState label="Loading usage" />
      ) : report.error || !report.data ? (
        <ErrorState message={report.error ?? "No data"} onRetry={report.reload} />
      ) : (
        <>
          <KeyValueGrid
            items={[
              { label: "Runs", value: report.data.totals.runs.toLocaleString() },
              { label: "Model calls", value: report.data.totals.calls.toLocaleString() },
              {
                label: "Tokens",
                value: `${report.data.totals.prompt_tokens.toLocaleString()} in · ${report.data.totals.completion_tokens.toLocaleString()} out`,
              },
              { label: "Cost", value: formatUsd(report.data.totals.cost_usd) },
            ]}
          />
          {report.data.by_model.length > 0 && (
            <div className="border-t border-line">
              <DataTable
                rows={report.data.by_model}
                rowKey={(row) => `${row.key}-${row.label}`}
                empty={{ title: "No calls" }}
                columns={[
                  { header: "Model", cell: (row) => <Mono>{row.key}</Mono> },
                  { header: "Role", width: "90px", cell: (row) => <TypeTag value={row.label ?? ""} /> },
                  { header: "Calls", width: "80px", align: "right", cell: (row) => row.calls },
                  {
                    header: "Tokens",
                    align: "right",
                    cell: (row) => (row.prompt_tokens + row.completion_tokens).toLocaleString(),
                  },
                  { header: "Cost", width: "100px", align: "right", cell: (row) => formatUsd(row.cost_usd) },
                ]}
              />
            </div>
          )}
          {report.data.by_trigger.length > 0 && (
            <div className="border-t border-line">
              <DataTable
                rows={report.data.by_trigger}
                rowKey={(row) => row.key}
                empty={{ title: "No runs" }}
                columns={[
                  {
                    header: "Triggered by",
                    cell: (row) => (
                      <span>
                        <TypeTag value={row.key.split(":")[0]} />{" "}
                        <span className="text-ink">{row.label}</span>
                      </span>
                    ),
                  },
                  { header: "Runs", width: "80px", align: "right", cell: (row) => row.runs },
                  { header: "Calls", width: "80px", align: "right", cell: (row) => row.calls },
                  { header: "Cost", width: "100px", align: "right", cell: (row) => formatUsd(row.cost_usd) },
                ]}
              />
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
