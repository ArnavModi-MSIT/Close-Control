export function AutoClosedBanner() {
  return (
    <div className="mb-5 rounded-xl border-[1.5px] border-good/40 bg-good-soft p-4">
      <h4 className="mb-1 font-semibold text-good">Auto-closed by re-verification</h4>
      <p className="text-[0.86rem] text-ink/85">
        This case was previously open for human review. A later matcher re-run found the
        transaction genuinely clean — no exception of any kind remains, not merely reclassified
        as a different one — so the system closed it automatically. No human reviewed this
        specific closure. See the activity log below for the exact before/after exception types.
        Escalate to reopen it if this looks wrong.
      </p>
    </div>
  );
}
