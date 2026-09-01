"""Guards the direction of this project's core rule: "AI proposes,
deterministic code disposes."

Idea sharpened by checking a peer Razorpay buildathon repo
(SuryaSK-dev/razorpay-ai-finance-controller) past its README into its
actual tests/test_architecture_boundary.py, which found a real gap in
its own project: the rule was asserted in prose (README/ARCHITECTURE.md)
and enforced informally, but nothing tested that the deterministic core
never imports the AI layer -- only a much narrower check on one reporting
script existed. Checking this project's own real code first (grep, not
assumed): matching/, cash_position/, and ingestion/ genuinely never
import agent/, investigator/, or qa_agent/ -- the property already holds,
it just wasn't tested. This file closes that gap the same way, adapted to
this project's own module layout, with the same two-tier discipline:

    STATIC   no core module may reference agent/investigator/qa_agent
             (zero exemptions currently needed -- verified clean)

    RUNTIME  importing the core must not LOAD any of those packages, or
             an optional LLM provider SDK (anthropic, groq) -- the
             property that actually matters at execution time, since a
             function-local import inside a never-called branch is
             invisible to the static check alone

review_backend/, scripts/, and run_matcher.py are deliberately NOT part
of the "core" checked here -- they are the orchestration/API layer and
legitimately import agent/investigator/qa_agent to wire the system
together. The core is specifically the modules that COMPUTE a financial
fact: matching (what matched, what didn't), cash_position (aggregation
over the matcher's own output), and ingestion (bank format normalization).
None of those may depend on the layer that talks to a language model.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

CORE_PACKAGES = ("matching", "cash_position", "ingestion")
AGENT_PACKAGES = ("agent", "investigator", "qa_agent")

# No exemptions currently needed -- verified clean by grep before writing
# this test. If one is ever legitimately required, name it here with the
# reasoning, the same discipline the peer repo's own single exemption
# followed, rather than weakening the check below.
EXEMPTIONS: set[tuple[str, str]] = set()


def _core_files():
    """Every tracked .py file under one of CORE_PACKAGES."""
    for pkg in CORE_PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            yield str(path.relative_to(ROOT)).replace("\\", "/"), path


def _agent_imports(path: Path):
    """(lineno, module) for every agent/investigator/qa_agent reference
    in one file, at any AST depth (so a function-local import is still
    caught by the static pass, even though only the runtime pass can
    prove it's never actually loaded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in AGENT_PACKAGES:
                yield node.lineno, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in AGENT_PACKAGES:
                    yield node.lineno, alias.name


# ============================================================ STATIC ===

def test_the_core_does_not_import_the_agent_layer():
    """THE BOUNDARY, ASSERTED WHERE IT MATTERS. matching/, cash_position/,
    and ingestion/ compute financial facts; none of them may depend on the
    layer that talks to a language model, because that dependency is
    exactly what "AI proposes, deterministic code disposes" rules out."""
    violations = []
    for relative, path in _core_files():
        for lineno, module in _agent_imports(path):
            if (relative, module) in EXEMPTIONS:
                continue
            violations.append(f"{relative}:{lineno} imports {module}")

    assert not violations, (
        "the deterministic core imports the AI layer:\n  "
        + "\n  ".join(violations)
        + "\n\nThe agent layer may depend on the core. The core may not "
        "depend on the agent layer. If this import is deliberate, add it "
        "to EXEMPTIONS with the reasoning, rather than deleting this test."
    )


def test_no_stale_exemptions():
    """An exemption for an import that's since been removed is dead
    permission -- it would silently allow the dependency back later
    without anyone noticing. (Currently EXEMPTIONS is empty, so this is
    a no-op today -- it exists so the FIRST real exemption is held to the
    same discipline from day one, not added loosely later.)"""
    for relative, module in EXEMPTIONS:
        path = ROOT / relative
        assert path.exists(), f"exempted file {relative} no longer exists"
        found = any(m == module for _, m in _agent_imports(path))
        assert found, (
            f"{relative} no longer imports {module} -- delete this "
            "exemption rather than leaving it as dead permission"
        )


# =========================================================== RUNTIME ===

def test_importing_the_core_does_not_load_the_agent_layer():
    """THE STRONGER CHECK. Static analysis says where the text sits; this
    says what actually happens: importing the whole deterministic core
    must not pull agent/investigator/qa_agent -- and therefore no
    provider SDK -- into memory. Run in a subprocess with a clean
    interpreter, since the test session itself imports agent modules for
    other test files and would pollute sys.modules."""
    program = (
        "import sys; "
        "import matching.loaders, matching.blocking, matching.engine, "
        "matching.settlement_builder, matching.ledger_check, matching.report, "
        "matching.diagnostics, matching.root_cause; "
        "import cash_position.engine, cash_position.reconciliation_statement; "
        "import ingestion.warehouse, ingestion.connectors.suryaan, ingestion.connectors.northbridge; "
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('agent', 'investigator', 'qa_agent')); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-1500:]

    leaked = [m for m in completed.stdout.strip().split(",") if m]
    assert not leaked, (
        f"importing the deterministic core loaded {leaked} -- a new "
        "module-level dependency on the agent layer was added"
    )


def test_importing_the_core_does_not_load_a_provider_sdk():
    """Same property one level out: no anthropic/groq SDK in memory after
    importing the core. The failure a reviewer would actually notice --
    a reconciliation engine that can't start without an LLM SDK
    installed."""
    program = (
        "import sys; "
        "import matching.engine, cash_position.engine, ingestion.warehouse; "
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in ('anthropic', 'groq')); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    assert not [m for m in completed.stdout.strip().split(",") if m], (
        "importing the core loaded a provider SDK"
    )


# ================================= THE REVERSE DIRECTION IS ALLOWED ===

def test_the_agent_layer_may_depend_on_the_core():
    """Asserted so the rule above isn't mistaken for "these two must
    never touch". investigator/tools.py reading matching's own report
    output is the architecture working, not a violation."""
    tools_src = (ROOT / "investigator/tools.py").read_text(encoding="utf-8")
    assert "from matching.config import" in tools_src


if __name__ == "__main__":
    tests = [
        test_the_core_does_not_import_the_agent_layer,
        test_no_stale_exemptions,
        test_importing_the_core_does_not_load_the_agent_layer,
        test_importing_the_core_does_not_load_a_provider_sdk,
        test_the_agent_layer_may_depend_on_the_core,
    ]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} architecture-boundary tests passed.")
