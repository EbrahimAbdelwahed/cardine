# Cardine security policy

## Supported versions

Cardine is a private product and has no public release or supported public API.
Security fixes are coordinated against the current private checkout. Do not
publish a Cardine build, container, design archive, or vulnerability report.

## Reporting a vulnerability

Do not open a public issue containing an exploitable vulnerability, private
study material, credentials, design source, or provider payloads. Use the
private vulnerability-reporting feature for the Cardine repository, or contact
the owner through an already authorized private channel. Never create a public
issue merely to disclose sensitive details.

Include the affected version or commit, the smallest safe reproduction, impact,
and any suggested mitigation. Do not use real student data or active
credentials in a reproduction.

## Scope notes

The Cardine runtime stores canonical events and source blobs locally. Its
exports are intentionally allowlisted, but operators should still inspect
bundles before sharing them. Model credentials belong only in environment
variables; never put credential values in repository configuration, fixtures,
logs, reports, container layers, or design archives. Browser and HTTP code must
not become an authority for event or SQLite state.
