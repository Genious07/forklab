import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { api, type OrderOutcome } from "./api";
import { countAt } from "./components/ProcessMap";
import { ComparisonTable } from "./components/ComparisonTable";
const order: OrderOutcome = {
  order_id: "A",
  service_class: "standard",
  arrival_minute: 0,
  deadline_minute: 60,
  pick_start_minute: 10,
  pick_end_minute: 20,
  pack_start_minute: 25,
  pack_end_minute: 30,
  dispatched_minute: null,
  terminal: "missed_cutoff",
  blocked_on_stock: true,
  blocked_minutes: [5],
  is_late: true,
  wait_minutes: 15,
  cycle_minutes: null,
};
afterEach(() => vi.unstubAllGlobals());
it("does not count a packed missed-cutoff order as dispatched", () => {
  expect(countAt([order], 40)).toMatchObject({ dispatched: 0, held: 1 });
});
it("does not reveal future stock shortages", () => {
  expect(countAt([order], 2).blocked).toBe(0);
  expect(countAt([order], 7).blocked).toBe(1);
  expect(countAt([order], 12).blocked).toBe(0);
});
it("counts each arrived order in exactly one stage", () => {
  for (const time of [0, 10, 20, 25, 30, 60]) {
    const counts = countAt([order], time);
    expect(
      counts.waitingToPick +
        counts.picking +
        counts.waitingToPack +
        counts.packing +
        counts.dispatched +
        counts.held,
    ).toBe(1);
  }
});
it("fetches every page of order outcomes", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => Array(1000).fill(order),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [{ ...order, order_id: "last" }],
    });
  vi.stubGlobal("fetch", fetch);
  const rows = await api.orders("job", "baseline", false);
  expect(rows).toHaveLength(1001);
  expect(rows[1000].order_id).toBe("last");
  expect(fetch.mock.calls[1][0]).toContain("offset=1000");
});
it("renders a no-comparison state", () => {
  expect(renderToStaticMarkup(<ComparisonTable report={null} />)).toContain(
    "No comparison",
  );
});
