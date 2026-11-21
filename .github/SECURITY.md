# Security Policy

## Supported Versions

Security updates are applied to the latest release of `gitlab-watcher`.

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, report them privately using [GitHub Security Advisories](https://github.com/gyengus/gitlab-watcher/security/advisories/new).

When reporting a vulnerability, please include:
- A clear description of the issue and potential impact
- Steps to reproduce or a minimal proof of concept
- Affected versions and configuration details (with all tokens/secrets redacted)

## Security Considerations for Users

- Keep `GITLAB_TOKEN` and Discord webhook URLs in your configuration file with restricted file permissions (`chmod 600`).
- Review AI tool permissions and prompt sanitization settings before running `gitlab-watcher` against untrusted repositories or public issues.

