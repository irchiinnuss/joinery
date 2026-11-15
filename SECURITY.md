# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately
through [GitHub's private vulnerability reporting](https://github.com/irchiinnuss/joinery/security/advisories/new)
or by email to SECURITY_CONTACT_EMAIL.

Include what you found, how to reproduce it, the Joinery version
(`joinery --version`) and your platform. You should get a reply within a week.
Please give us a reasonable time to fix the problem before you disclose it.

## What counts

Anything that lets a project, a tool result or a model response escape the
offline tool workers, bypass the policy hook or the asset provenance checks,
read or leak an agent's credentials, or run code outside a reviewed change.

## Supported versions

Security fixes go into the latest release only.
