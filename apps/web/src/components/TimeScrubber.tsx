import { clock } from "../api";

interface Props {
  minute: number;
  max: number;
  onChange: (minute: number) => void;
  disabled: boolean;
  cutoffMinute: number;
}

/* One scrubber drives both lanes, so baseline and candidate are always compared
   at the same simulated time. */
export function TimeScrubber({ minute, max, onChange, disabled, cutoffMinute }: Props) {
  return (
    <div className="scrubber">
      <div className="head">
        <label htmlFor="shared-time">Shared time</label>
        <span className="clock">{clock(minute)}</span>
        <span className="footnote">
          dispatch cutoff at {clock(cutoffMinute)}
          {minute > cutoffMinute ? ", passed" : ""}
        </span>
      </div>
      <input
        id="shared-time"
        type="range"
        min={0}
        max={max}
        step={5}
        value={minute}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-valuetext={`${clock(minute)}, minute ${minute} of the facility day`}
      />
      <div className="ends">
        <span>{clock(0)}</span>
        <span>{clock(max)}</span>
      </div>
    </div>
  );
}
