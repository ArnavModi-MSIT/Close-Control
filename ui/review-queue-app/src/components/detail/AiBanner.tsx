export function AiBanner() {
  return (
    <div className="mb-5 rounded-xl border-[1.5px] border-accent/40 bg-accent-soft p-4">
      <h4 className="mb-1 font-semibold text-accent">Auto-resolved by the deterministic gate</h4>
      <p className="text-[0.86rem] text-ink/85">
        The AI proposed a resolution; every one of the gate's seven conditions held simultaneously,
        so the system authorized it without a human — the gate decided, the AI only proposed. No
        human has reviewed this case yet. If that looks wrong, revert it below to send it into the
        normal review flow.
      </p>
    </div>
  );
}
