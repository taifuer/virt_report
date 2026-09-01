# Data Policy

This document explains what virt-report collects, publishes, and excludes. It
describes repository and project policy; upstream sources remain governed by
their own terms.

## Data Sources and Purpose

virt-report reads public QEMU, KVM, and libvirt mailing lists, issue trackers,
project sites, and conference catalogues. It uses those records to identify
technical activity and produce Chinese daily, weekly, monthly, topic, and
conference summaries. Source links are retained so readers can verify the
context.

## What May Be Published

Public reports and the generated `site/` snapshot may contain:

- titles, dates, project and source names, counts, and stable source links;
- limited excerpts needed to identify or explain a discussion;
- project-authored classifications, commentary, and AI-assisted summaries;
- curated conference metadata with source attribution.

Project-authored editorial text and AI-assisted summaries are offered under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) to the extent that
the project holds applicable rights. Credit `virt-report`, link to the report,
and indicate material changes. Third-party names, trademarks, metadata,
excerpts, and linked content retain their original ownership and licensing;
the Apache-2.0 software license does not apply to them.

## What Must Not Be Published

The runtime `data/` directory, SQLite databases, raw mailbox archives, collector
caches, logs, backups, access keys, API credentials, private metrics, and raw
authorization headers must never be committed or included in a public source
release. Test fixtures must be synthetic or minimized and must not contain
secrets or unnecessary personal data.

Generated snapshots are derived publications, not complete mirrors of upstream
archives. Collect and retain only the fields needed for reporting, and avoid
republishing full messages when a source link and short context are sufficient.

## External Model Processing

When AI reporting is enabled, virt-report sends selected public titles and
minimized discussion excerpts to the configured DeepSeek API to produce a
structured summary. It does not intentionally send API keys, authorization
headers, private metrics, local logs, or raw archive files. Operators are
responsible for reviewing the model provider's current terms and configuring
retention and access controls appropriate to their deployment.

## Hosted-Site Analytics

The hosted site at `virt.taifua.com` uses Baidu Analytics to understand aggregate
page visits. This integration is injected by the production deployment, is not
included in the repository source, and is not required for self-hosting. A
visitor's browser may therefore connect to Baidu's analytics service when using
the hosted site. Self-hosting operators are responsible for disclosing any
analytics they add and for following applicable laws and provider terms.

## Contributions and Corrections

New sources must be public, relevant to virtualization, and documented with a
canonical URL and collection method. Contributors must not submit private,
embargoed, paywalled, or unlawfully obtained material.

To report an incorrect attribution, misleading summary, stale link, or request
review of published material, email [taifu@taifua.com](mailto:taifu@taifua.com)
with the page URL and the requested correction.
