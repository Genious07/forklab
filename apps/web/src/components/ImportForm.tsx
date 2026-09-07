import { useState } from "react";

export function ImportForm({
  onImported,
  disabled,
}: {
  onImported: (id: string) => Promise<void>;
  disabled: boolean;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <details className="import-panel">
      <summary>Use your warehouse CSVs</summary>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setMessage("");
          const data = new FormData(e.currentTarget);
          const optional = data.get("replenishments_file");
          if (optional instanceof File && !optional.size)
            data.delete("replenishments_file");
          try {
            const response = await fetch("/api/imports", {
              method: "POST",
              body: data,
            });
            const body = await response.json();
            if (!response.ok)
              throw new Error(
                typeof body.detail === "string"
                  ? body.detail
                  : JSON.stringify(body.detail),
              );
            setMessage(
              body.message +
                (body.missing_skus?.length
                  ? " Missing inventory SKUs: " + body.missing_skus.join(", ")
                  : "") +
                (body.issues?.length
                  ? " " +
                    body.issues
                      .map((i: { line_number: number; reason: string }) =>
                        JSON.stringify(i),
                      )
                      .join("; ")
                  : ""),
            );
            if (body.scenario_id) await onImported(body.scenario_id);
          } catch (err) {
            setMessage((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="footnote">
          One facility day. Up to 5,000 orders; 2 MiB per UTF-8 file. Use
          numeric minutes from 08:00. Facility capacity and processing times
          start as estimates; inspect them before interpreting results.
        </p>
        <div className="import-fields">
          <label className="field">
            Orders CSV
            <input
              required
              type="file"
              name="orders_file"
              accept=".csv,text/csv"
            />
            <span className="hint">
              order_id, sku, quantity, arrival, deadline, service_class
            </span>
          </label>
          <label className="field">
            Inventory CSV
            <input
              required
              type="file"
              name="inventory_file"
              accept=".csv,text/csv"
            />
            <span className="hint">sku, on_hand</span>
          </label>
          <label className="field">
            Replenishments CSV (optional)
            <input
              type="file"
              name="replenishments_file"
              accept=".csv,text/csv"
            />
            <span className="hint">sku, quantity, arrival</span>
          </label>
        </div>
        <button className="btn" disabled={disabled || busy}>
          {busy ? "Checking CSVs" : "Import dataset"}
        </button>
        <p role="status" className="import-message">
          {message}
        </p>
      </form>
    </details>
  );
}
