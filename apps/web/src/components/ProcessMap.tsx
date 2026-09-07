import type { OrderOutcome } from "../api";
import { clock } from "../api";

/* The map is schematic. Position encodes process flow, not floor geometry.
   Counts are the numerical authority; the map explains mechanics. */

export interface StageCounts {
  waitingToPick: number;
  picking: number;
  waitingToPack: number;
  packing: number;
  dispatched: number;
  blocked: number;
  held: number;
}

export function countAt(orders: OrderOutcome[], minute: number): StageCounts {
  const counts: StageCounts = {
    waitingToPick: 0,
    picking: 0,
    waitingToPack: 0,
    packing: 0,
    dispatched: 0,
    blocked: 0,
    held: 0,
  };
  for (const order of orders) {
    if (order.arrival_minute > minute) continue;

    const pickStart = order.pick_start_minute;
    const pickEnd = order.pick_end_minute;
    const packStart = order.pack_start_minute;
    const packEnd = order.pack_end_minute;
    const dispatched = order.dispatched_minute;

    if (dispatched !== null && dispatched <= minute) {
      counts.dispatched += 1;
    } else if (
      packStart !== null &&
      packStart <= minute &&
      (packEnd === null || packEnd > minute)
    ) {
      counts.packing += 1;
    } else if (
      pickEnd !== null &&
      pickEnd <= minute &&
      (packStart === null || packStart > minute)
    ) {
      counts.waitingToPack += 1;
    } else if (
      pickStart !== null &&
      pickStart <= minute &&
      (pickEnd === null || pickEnd > minute)
    ) {
      counts.picking += 1;
    } else if (pickStart === null || pickStart > minute) {
      counts.waitingToPick += 1;
      if (order.blocked_minutes?.some((at) => at <= minute))
        counts.blocked += 1;
    } else {
      counts.held += 1;
    }
  }
  return counts;
}

function Ticks({
  value,
  capacity,
  cumulative,
}: {
  value: number;
  capacity: number;
  cumulative: boolean;
}) {
  const shown = Math.min(value, 24);
  /* Orange means attention. A cumulative counter is volume, not pressure, so it
     never turns orange no matter how large it grows. */
  const pressured = !cumulative && value > capacity;
  return (
    <div className="queue-bar" aria-hidden="true">
      {Array.from({ length: shown }, (_, i) => (
        <i key={i} className={pressured ? "pressured" : ""} />
      ))}
    </div>
  );
}

interface StageProps {
  name: string;
  sub: string;
  count: number;
  unit: string;
  capacity: number;
  pressured: boolean;
  cumulative?: boolean;
}

function Stage({
  name,
  sub,
  count,
  unit,
  capacity,
  pressured,
  cumulative = false,
}: StageProps) {
  return (
    <div className={`stage${pressured ? " pressured" : ""}`}>
      <StageIcon name={name} />
      <div className="name">{name}</div>
      <div className="sub">{sub}</div>
      <div className="count">
        {count}
        <span className="unit">{unit}</span>
      </div>
      <Ticks value={count} capacity={capacity} cumulative={cumulative} />
    </div>
  );
}

interface Props {
  label: string;
  variant: "baseline" | "candidate";
  orders: OrderOutcome[];
  minute: number;
  pickers: number;
  packingStations: number;
}

export function ProcessMap({
  label,
  variant,
  orders,
  minute,
  pickers,
  packingStations,
}: Props) {
  const c = countAt(orders, minute);
  const alternative = [
    `At ${clock(minute)} in the ${label} lane:`,
    `  waiting to pick ${c.waitingToPick}${c.blocked ? ` (${c.blocked} with a recorded stock shortage)` : ""}`,
    `  picking ${c.picking} of ${pickers} pickers busy`,
    `  waiting to pack ${c.waitingToPack}`,
    `  packing ${c.packing} of ${packingStations} stations busy`,
    `  dispatched so far ${c.dispatched}`,
    `  packed but not dispatched ${c.held}`,
  ].join("\n");

  return (
    <section aria-label={`Process map, ${label}`}>
      <div className={`lane-label ${variant}`}>
        <span className="swatch" aria-hidden="true" />
        {label}
      </div>
      <div className="stage-row">
        <Stage
          name="Receiving"
          sub={
            c.blocked > 0
              ? `${c.blocked} with a recorded stock shortage`
              : "Inbound stock"
          }
          count={c.waitingToPick}
          unit="waiting"
          capacity={12}
          pressured={c.blocked > 0}
        />
        <Stage
          name="Picking"
          sub={`${pickers} pickers`}
          count={c.picking}
          unit={`of ${pickers} busy`}
          capacity={pickers}
          pressured={c.picking >= pickers}
        />
        <Stage
          name="Packing"
          sub={`${c.waitingToPack} waiting / ${packingStations} stations`}
          count={c.packing}
          unit={`of ${packingStations} busy`}
          capacity={packingStations}
          pressured={c.waitingToPack > packingStations * 3}
        />
        <Stage
          name="Dispatch"
          sub={`${c.held} packed, not dispatched`}
          count={c.dispatched}
          unit="sent"
          capacity={24}
          pressured={false}
          cumulative
        />
      </div>
      <details className="text-alternative">
        <summary>Text equivalent of this map</summary>
        <pre>{alternative}</pre>
      </details>
    </section>
  );
}

function StageIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    Receiving:
      "M6 32V13l18-8 18 8v19M12 32V18h24v14M17 32v-9h14v9M4 37h40M10 41h28",
    Picking:
      "M5 8h26v28H5zM5 18h26M5 28h26M18 8v28M35 15h8v18h-8M35 24h8M9 41h30",
    Packing:
      "M8 17l16-9 16 9v20l-16 9-16-9zM8 17l16 9 16-9M24 26v20M16 12l16 9v9",
    Dispatch:
      "M4 13h25v23H4zM29 22h8l7 8v6H29M34 22v8h10M10 36a4 4 0 1 0 8 0M31 36a4 4 0 1 0 8 0",
  };
  return (
    <svg className="stage-icon" viewBox="0 0 48 50" aria-hidden="true">
      <path
        d={paths[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}
