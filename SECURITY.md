# Security Policy

## Supported versions

The latest release is the only supported version.

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://github.com/jquezada19/vv-cli/security/advisories/new)
rather than opening a public issue.

## Threat model

`vv` is a local CLI. It takes no network input, opens no sockets, and has no
dependencies beyond the Python and Rust standard libraries. The security-
relevant surface is what it does to files you point it at:

- **Containment.** Every read and write path — including `rename`/`move`
  destinations — resolves through a realpath check that refuses to leave the
  vault root. A path-traversal escape from that check is a vulnerability.
- **Destructive writes.** Writers are compare-and-swap guarded, and refactors
  are journaled with hash-manifested backups. Any input that causes `vv` to
  lose a note's contents, clobber another writer's bytes during rollback, or
  leave an unrecoverable journal is a vulnerability.
- **Not in scope.** `vv` trusts the vault you give it. Malicious note *content*
  (a hostile wikilink, a crafted heading) should never cause writes outside the
  vault, but is otherwise treated as data to parse, not as a trust boundary.
