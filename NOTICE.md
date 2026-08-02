# Cardine ownership and licensing boundary

Cardine is a private product repository. It is not an open-source Cardine
distribution, and this repository makes no public release or PyPI publication
claim.

This checkout contains material with three distinct ownership and licensing
categories:

1. **Copied Harness core.** The provider-neutral runtime, developer CLI, and
   their inherited tests and documentation were copied from the public
   `study-agent-harness` baseline at commit `e18f670`. Those inherited
   materials remain under the Apache License, Version 2.0. The accompanying
   root [`LICENSE`](LICENSE) is retained for that core; this file does not
   relicense or narrow it.
2. **Third-party assets.** Bundled fonts and icons retain their upstream
   licenses and attribution notices. See
   [`src/study_agent/demo/fonts/LICENSE.txt`](src/study_agent/demo/fonts/LICENSE.txt)
   and
   [`src/study_agent/demo/icons/LICENSE.phosphor.txt`](src/study_agent/demo/icons/LICENSE.phosphor.txt).
   Those notices govern the corresponding asset files and are not replaced by
   Cardine's private ownership notice.
3. **Private Cardine product work.** Cardine's product shell, private auth and
   settings, product-only application composition, deployment files, and
   product design source are owned by Ebrahim Abdelwahed and are not licensed
   for reuse. The private boundary is recorded in
   [`LICENSE-CARDINE.md`](LICENSE-CARDINE.md). Where a source file carries an
   upstream notice, that notice controls instead.

The Python package keeps the internal `study_agent` namespace and protocol
identifiers for compatibility. That implementation detail does not change the
ownership boundary above.

## Design-source custody

The preserved design archive is imported at
[`docs/design-source/prototype-study-agent-ui.zip`](docs/design-source/prototype-study-agent-ui.zip).
It was recovered from the approved preservation bundle at
`/private/tmp/cardine-migration-preservation/source/prototype-study-agent-ui.zip`,
which was verified byte-for-byte against the original source recorded in
`metadata/source-origins.tsv`:

```text
origin: /Users/ebrahimabdelwahed/Desktop/Med/Lezioni/Audio_to_Sbobina/study-agent-ui/Study Agent per Medicina.zip
sha256: f1449232643deed809b5d66fea3f65809bb180ab880bbc20c83ff900a6a9cf19
```

The archive is private design evidence, not a public asset bundle. Do not
publish or redistribute it without the owner's permission.
