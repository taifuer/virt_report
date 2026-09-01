# Security Policy

## Supported Versions

Security fixes are applied to the current `main` branch. Container images are
built locally from the repository; the project does not currently maintain a
separate, versioned registry-image release line. Older commits, local
modifications, and archived images are not maintained as separate release lines.

## Reporting a Vulnerability

Do not open a public issue for a suspected vulnerability. Email
[taifu@taifua.com](mailto:taifu@taifua.com) with the subject
`[virt-report security]` and include:

- the affected component and version or commit;
- reproduction steps and the expected security impact;
- relevant logs or a minimal proof of concept, with secrets removed;
- any known workaround or disclosure deadline.

Reports concerning authentication bypass, exposed credentials, unsafe handling
of upstream content, request forgery, injection, cross-site scripting, or
dependency compromise are in scope. Data corrections and summary-quality issues
should follow [DATA_POLICY.md](DATA_POLICY.md).

Please allow time to reproduce and address the issue before public disclosure.
The maintainer will coordinate status and disclosure with the reporter when
contact information is available.

## Deployment Guidance

Keep `DEEPSEEK_API_KEY`, `GITLAB_TOKEN`, and metrics access credentials outside
images and source control. Restrict runtime data permissions, place the web
service behind a maintained TLS reverse proxy, and expose protected operational
metrics only to intended users. Review collector URLs and configuration changes
before deploying them to a trusted network.
