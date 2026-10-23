# Security policy

## Supported scope

Security fixes target current `main` and the latest release candidate. The project
is experimental; older releases may require upgrading. Release-candidate status
does not establish manufacturing acceptance or guarantee upstream availability.

The optional Altium extra currently retains known vulnerable upstream pins.
It is excluded from the default installation; see the affected versions,
restrictions and next review date in [dependency-security.md](docs/dependency-security.md).

## Reporting a vulnerability

Use **Security → Advisories → Report a vulnerability** on GitHub when private
reporting is enabled. Include the affected version/commit, reproduction steps,
impact and a minimal sanitized example. Do not include working credentials or a
private component library.

If the private-report button is unavailable, open an issue requesting a private
contact channel, with no exploit details, credentials or sensitive attachments.
Ordinary bugs can use public issues with a sanitized reproduction.

## Local trust boundary

- Prefer stdio under a dedicated local account. Clients and tool-call approvals
  control access to the server's local capabilities.
- HTTP/SSE are loopback-only CLI transports with Host/Origin checks. They have no
  per-user authentication and are not a public multi-user service.
- File and CAD tools run with the server account's filesystem permissions. There
  is no general filesystem sandbox; use disposable copies for write tests.
- Keep master libraries backed up. Respect dry-run, conflict and backup results;
  automatic checks do not replace engineering review.
- Use separate archive/CAD roots for separate server processes.
- Tool results, agent transcripts and validation logs can contain absolute paths,
  upstream responses and engineering-library names. Review and redact copies
  before posting them; keep original evidence privately.
- Store credentials in local environment/configuration, never in examples,
  fixtures, issues or Git history. Upstream access restrictions must be honored.

## GitHub Actions and local validation

GitHub-generated PR test-merge commits can carry account-level email metadata
independent of local Git configuration. Before public PRs, enable account email
privacy and verify generated metadata in a private rehearsal. See GitHub's
[commit email settings](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address).
The history guard detects violations after generation; it does not prevent the
initial disclosure or purge GitHub-owned references and cached objects.

Public contributions run on GitHub-hosted runners. Release publication is an
explicit `workflow_dispatch` from `main`; published tags and assets are not
overwritten by the workflow.

The inactive Antigravity template in `examples/maintainer-private-validation.yml`
is for a separately secured private repository only. It is not a workflow of
this public repository.
**Do not attach developer workstations as self-hosted runners to this public repository.**
Workflow conditions and first-time-contributor approval are not a sandbox for a
developer workstation. Run the local validation harness directly, or place
private machine automation in a separately secured private repository.

GitHub documents this boundary in its
[self-hosted runner security guidance](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).
