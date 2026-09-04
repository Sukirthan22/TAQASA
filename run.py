"""
run.py — the single entry point for the whole project.

Plain English: everything you can do with this repo, you do through this file.

    python run.py --generate          build data/invoices.csv          (Phase 1)
    python run.py --verify            run the Gate 1 checks            (Phase 1)
    python run.py --policy baseline   score the dumb ladder            (Phase 2)
    python run.py --infer             score both inference arms        (Phase 3)
    python run.py --policy agent      score the smart agent            (Phase 4)
    python run.py --compare           write results/report.md          (Phase 5)

Phases that have not been built yet fail loudly rather than pretending.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the repo root importable so `import config` works from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg  # noqa: E402


def cmd_generate(args) -> int:
    """Phase 1: build the invoice table and write it to disk."""
    from src.simulator import generate_invoices, write_invoices

    df = generate_invoices(n=args.n, seed=args.seed)
    path = write_invoices(df)

    print(f"Wrote {len(df)} invoices to {path} (seed={args.seed})")
    print()
    print("Cause mix:")
    counts = df["latent_cause"].value_counts()
    for cause in cfg.CAUSES:
        n = int(counts.get(cause, 0))
        print(f"  {cause:<12} {n:>4}  {n / len(df):>6.1%}   (target {cfg.CAUSE_SHARES[cause]:.0%})")
    print()
    print(f"Invoices with deliberately misleading clues: "
          f"{int(df['hidden_clue_noise'].sum())} ({df['hidden_clue_noise'].mean():.1%})")
    print(f"Invoices above the Rs {cfg.CHRONIC_LEGAL_MIN_AMOUNT:,} legal threshold: "
          f"{int((df['amount'] > cfg.CHRONIC_LEGAL_MIN_AMOUNT).sum())}")
    return 0


def cmd_verify(args) -> int:
    """Phase 1: run every Gate 1 check and report pass/fail."""
    from verify_phase1 import main as verify_main

    return verify_main()


def cmd_policy(args) -> int:
    """Phase 2/4: run one policy over every invoice and print its scoreboard."""
    from src.harness import print_result, run_policy
    from src.simulator import load_invoices

    if args.policy == "baseline":
        from src.policies.baseline import build
    else:
        from src.policies.agent import build

    df = load_invoices()
    result = run_policy(build(), df)
    print_result(result)

    # The agent writes its reasoning to the append-only audit log. The baseline
    # has no reasoning to write, so it does not.
    if args.policy == "agent":
        from src.audit import summarise, write_run

        path = write_run(result)
        stats = summarise(path)
        print("-" * 76)
        print(f"AUDIT LOG  {path}")
        print(f"  {stats['rows']:,} rows, one per decision")
        print(f"  {stats['veto_total']:,} of them are guardrail vetoes:")
        for rule, count in sorted(stats["vetoes"].items()):
            print(f"      {count:>5}  {rule}")
        print()
    return 0


def cmd_infer(args) -> int:
    """Phase 3: train both inference arms and score them on the held-out split."""
    from src.inference import evaluate
    from src.simulator import load_invoices

    evaluate(load_invoices(), seed=args.seed)
    return 0


def cmd_compare(args) -> int:
    raise NotImplementedError(
        "--compare is Phase 5. Gates 1-4 must pass first."
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="B2B Receivables Chaser — simulate, chase, and score overdue invoices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--generate", action="store_true",
                   help="Phase 1: generate data/invoices.csv")
    p.add_argument("--verify", action="store_true",
                   help="Phase 1: run the Gate 1 checks")
    p.add_argument("--policy", choices=["baseline", "agent"],
                   help="Phase 2/4: run one policy over all invoices and score it")
    p.add_argument("--infer", action="store_true",
                   help="Phase 3: score both cause-inference arms on the held-out split")
    p.add_argument("--compare", action="store_true",
                   help="Phase 5: run both policies and write results/report.md")
    p.add_argument("--n", type=int, default=cfg.N_INVOICES,
                   help=f"number of invoices to generate (default {cfg.N_INVOICES})")
    p.add_argument("--seed", type=int, default=cfg.SEED,
                   help=f"random seed (default {cfg.SEED})")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.generate:
        return cmd_generate(args)
    if args.verify:
        return cmd_verify(args)
    if args.policy:
        return cmd_policy(args)
    if args.infer:
        return cmd_infer(args)
    if args.compare:
        return cmd_compare(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
