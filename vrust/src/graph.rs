// graph.rs — full-rewrite module (agent-built; see docs/rust-rewrite-plan.md).
// Contract: happy path only; ANY doubt returns Outcome::Fallback and main()
// execs the Python implementation. Byte parity is pinned by differential tests.
use crate::readpath::Outcome;
use std::path::Path;

#[allow(unused_variables)]
pub fn run(cmd: &str, args: &[String], vault: &Path) -> Outcome {
    Outcome::Fallback   // stub: module not yet implemented
}
