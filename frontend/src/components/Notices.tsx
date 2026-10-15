import { Icon } from "./Icon";

// Fixed product captions: FR-406 / BR-406.1, TRD §10.6 and FRD L-01.
// Also cover metric surfaces whose current API has no caption field.
export const SIGNAL_NOTICE =
  "Sinyal ini merupakan observasi yang mendukung investigasi dan bukan bukti adanya perbuatan salah. This signal is an observation that supports an investigation; it is not evidence of wrongdoing.";
export const SYNTHETIC_NOTICE =
  "Synthetic scenarios and/or public-source observations are used for demonstration; model scores are not proof of fraud or production detection performance (TRD §10.6, FRD L-01).";

export function SignalNotice({ text = SIGNAL_NOTICE }: { text?: string }) {
  return (
    <div className="notice signal-notice">
      <Icon name="info" size={18} />
      <p>{text || SIGNAL_NOTICE}</p>
    </div>
  );
}

export function SyntheticNotice({
  text = SYNTHETIC_NOTICE,
}: {
  text?: string;
}) {
  return (
    <div className="synthetic-notice">
      <Icon name="layers" size={17} />
      <p>
        <strong>Data demonstrasi</strong>
        <span>{text || SYNTHETIC_NOTICE}</span>
      </p>
    </div>
  );
}
