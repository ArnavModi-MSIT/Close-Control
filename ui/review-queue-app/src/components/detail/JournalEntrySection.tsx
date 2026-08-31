import { DetailSection } from "./DetailSection";
import { rupees } from "../../lib/format";
import type { JournalEntry } from "../../types";

export function JournalEntrySection({ entry }: { entry: JournalEntry }) {
  return (
    <DetailSection
      title="Drafted journal entry"
      badge={
        <span
          className={`rounded-full px-2 py-0.5 font-mono text-[0.66rem] font-semibold tracking-wide normal-case ${
            entry.balanced ? "bg-good-soft text-good" : "bg-warn text-ink"
          }`}
          title={
            entry.balanced
              ? "Total debits equal total credits — this entry is arithmetically sound to post."
              : "Debits and credits do not match — this entry must NOT be posted as-is."
          }
        >
          {entry.balanced ? "BALANCED" : "NOT BALANCED"}
        </span>
      }
    >
      <p className="mb-2 text-[0.82rem] text-ink-soft">
        A proposed double-entry posting for this case, drafted deterministically from the matcher's
        own numbers — no LLM involved, since the accounting treatment per exception type is fixed,
        known practice, not a judgment call. Not posted anywhere; a human reviewer decides that.
      </p>
      <p className="mb-2.5 text-[0.84rem] text-ink">{entry.narration}</p>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-[0.8rem]">
          <thead>
            <tr className="border-b border-border bg-surface-2 text-left text-[0.68rem] tracking-wide text-ink-mute uppercase">
              <th className="px-2.5 py-1.5">Account</th>
              <th className="px-2.5 py-1.5 text-right">Debit</th>
              <th className="px-2.5 py-1.5 text-right">Credit</th>
            </tr>
          </thead>
          <tbody>
            {entry.lines.map((line, i) => (
              <tr key={i} className="border-b border-border last:border-b-0">
                <td className="px-2.5 py-1.5">
                  <span className="font-mono text-[0.72rem] text-ink-mute">{line.account_code}</span>{" "}
                  {line.account_name}
                </td>
                <td className="px-2.5 py-1.5 text-right font-mono">
                  {line.side === "DR" ? rupees(line.amount_rupees) : ""}
                </td>
                <td className="px-2.5 py-1.5 text-right font-mono">
                  {line.side === "CR" ? rupees(line.amount_rupees) : ""}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-[1.5px] border-border-2 font-semibold">
              <td className="px-2.5 py-1.5 text-ink-soft">Total</td>
              <td className="px-2.5 py-1.5 text-right font-mono">{rupees(entry.total_debits_rupees)}</td>
              <td className="px-2.5 py-1.5 text-right font-mono">{rupees(entry.total_credits_rupees)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </DetailSection>
  );
}
