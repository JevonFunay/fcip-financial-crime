// Fixed product captions: FR-406 / BR-406.1, TRD §10.6 and FRD L-01.
// Also cover metric surfaces whose current API has no caption field.
export const SIGNAL_NOTICE =
  "Sinyal ini merupakan observasi yang mendukung investigasi dan bukan bukti adanya perbuatan salah. This signal is an observation that supports an investigation; it is not evidence of wrongdoing.";
export const SYNTHETIC_NOTICE =
  "Synthetic scenarios and/or public-source observations are used for demonstration; model scores are not proof of fraud or production detection performance (TRD §10.6, FRD L-01).";

export function SignalNotice({ text = SIGNAL_NOTICE }: { text?: string }) {
  return (
    <p className="signal-notice">{text || SIGNAL_NOTICE}</p>
  );
}

export function SyntheticNotice({
  text = SYNTHETIC_NOTICE,
}: {
  text?: string;
}) {
  return (
    <p className="synthetic-notice">{text || SYNTHETIC_NOTICE}</p>
  );
}
